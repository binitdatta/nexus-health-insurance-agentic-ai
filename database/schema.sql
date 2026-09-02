-- =====================================================================
-- Nationwide Health Insurance - Agentic AI Chatbot Platform
-- Central MySQL 8.x Schema
--
-- Covers:
--   1. Platform/central tables (departments, users, LLM+HTTP logging,
--      HITL task queue, RAG knowledge docs, cost summary rollups)
--   2. Department-specific transactional (domain) tables, one per
--      department, each carrying dept_id so the central log/cost
--      tables and the domain tables can always be joined/filtered
--      consistently.
--
-- Target: MySQL 8.0+, InnoDB, utf8mb4
-- =====================================================================

CREATE DATABASE IF NOT EXISTS health_ai_platform
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE health_ai_platform;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ---------------------------------------------------------------------
-- 1. PLATFORM CORE
-- ---------------------------------------------------------------------

DROP TABLE IF EXISTS departments;
CREATE TABLE departments (
  dept_id             INT AUTO_INCREMENT PRIMARY KEY,
  dept_code           VARCHAR(20)  NOT NULL UNIQUE,
  dept_name           VARCHAR(100) NOT NULL,
  description         VARCHAR(255),
  keycloak_client_id  VARCHAR(100) NOT NULL,
  chatbot_base_url    VARCHAR(255),
  is_active           TINYINT(1)   NOT NULL DEFAULT 1,
  created_at          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

DROP TABLE IF EXISTS app_users;
CREATE TABLE app_users (
  user_id       INT AUTO_INCREMENT PRIMARY KEY,
  keycloak_sub  VARCHAR(64)  NOT NULL UNIQUE,
  username      VARCHAR(100) NOT NULL,
  full_name     VARCHAR(150),
  email         VARCHAR(150),
  dept_id       INT          NOT NULL,
  realm_roles   VARCHAR(255),
  job_title     VARCHAR(100),
  is_active     TINYINT(1)   NOT NULL DEFAULT 1,
  created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_users_dept (dept_id),
  CONSTRAINT fk_users_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

-- Central LLM call log: every call any department chatbot routes through
-- the central Flask LLM Gateway to Anthropic gets one row here.
DROP TABLE IF EXISTS llm_call_log;
CREATE TABLE llm_call_log (
  log_id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  request_id        VARCHAR(64)  NOT NULL,
  dept_id           INT          NOT NULL,
  user_id           INT          NULL,
  chatbot_source    VARCHAR(50)  NOT NULL,   -- e.g. 'claims-chatbot'
  session_id        VARCHAR(64),
  model_name        VARCHAR(100) NOT NULL,   -- e.g. 'claude-sonnet-4-6'
  operation         VARCHAR(50)  NOT NULL,   -- INTENT_DETECTION | RESPONSE_FINALIZATION | RAG_ANSWER | HITL_DRAFT
  endpoint          VARCHAR(150) NOT NULL,   -- Anthropic endpoint called
  intent_detected   VARCHAR(100),
  request_payload   JSON,
  response_payload  JSON,
  prompt_tokens     INT          NOT NULL DEFAULT 0,
  completion_tokens INT          NOT NULL DEFAULT 0,
  total_tokens      INT          NOT NULL DEFAULT 0,
  input_cost_usd    DECIMAL(12,6) NOT NULL DEFAULT 0,
  output_cost_usd   DECIMAL(12,6) NOT NULL DEFAULT 0,
  total_cost_usd    DECIMAL(12,6) NOT NULL DEFAULT 0,
  latency_ms        INT,
  http_status        INT,
  call_status       VARCHAR(20)  NOT NULL DEFAULT 'SUCCESS', -- SUCCESS | ERROR | TIMEOUT
  error_message     TEXT,
  created_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_llm_dept_created (dept_id, created_at),
  INDEX idx_llm_request (request_id),
  INDEX idx_llm_chatbot (chatbot_source, created_at),
  CONSTRAINT fk_llm_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id),
  CONSTRAINT fk_llm_user FOREIGN KEY (user_id) REFERENCES app_users(user_id)
) ENGINE=InnoDB;

-- HTTP call log: every chatbot UI -> central API (or any other internal)
-- HTTP call, logged both to a flat file (by the Flask app) and here.
DROP TABLE IF EXISTS http_call_log;
CREATE TABLE http_call_log (
  log_id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  request_id       VARCHAR(64)  NOT NULL,
  dept_id          INT          NOT NULL,
  user_id          INT          NULL,
  chatbot_source   VARCHAR(50)  NOT NULL,
  session_id       VARCHAR(64),
  http_method      VARCHAR(10)  NOT NULL,
  endpoint         VARCHAR(255) NOT NULL,
  target_service   VARCHAR(100) NOT NULL DEFAULT 'central-llm-api',
  request_payload  JSON,
  response_payload JSON,
  response_status  INT,
  latency_ms       INT,
  client_ip        VARCHAR(64),
  created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_http_dept_created (dept_id, created_at),
  INDEX idx_http_chatbot (chatbot_source, created_at),
  CONSTRAINT fk_http_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id),
  CONSTRAINT fk_http_user FOREIGN KEY (user_id) REFERENCES app_users(user_id)
) ENGINE=InnoDB;

-- Human-in-the-loop task queue: AI-proposed data creation/changes that
-- need a human approval before being committed to a domain table.
-- Supports BOTH inline chat approval and a standalone review queue view.
DROP TABLE IF EXISTS hitl_task_queue;
CREATE TABLE hitl_task_queue (
  task_id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  dept_id              INT          NOT NULL,
  chatbot_source       VARCHAR(50)  NOT NULL,
  session_id           VARCHAR(64),
  requested_by_user_id INT,
  task_type            VARCHAR(50)  NOT NULL,   -- CREATE | UPDATE | CLOSE
  entity_type          VARCHAR(50)  NOT NULL,   -- e.g. 'claims', 'prior_authorizations'
  entity_ref_id        BIGINT,                  -- NULL for CREATE until approved
  proposed_payload     JSON         NOT NULL,
  original_payload     JSON,
  ai_rationale         TEXT,
  status               VARCHAR(20)  NOT NULL DEFAULT 'PENDING', -- PENDING|APPROVED|REJECTED|EDITED|CANCELLED
  reviewer_user_id     INT,
  review_notes         VARCHAR(500),
  reviewed_at          TIMESTAMP    NULL,
  created_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_hitl_dept_status (dept_id, status),
  INDEX idx_hitl_session (session_id),
  CONSTRAINT fk_hitl_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id),
  CONSTRAINT fk_hitl_req_user FOREIGN KEY (requested_by_user_id) REFERENCES app_users(user_id),
  CONSTRAINT fk_hitl_rev_user FOREIGN KEY (reviewer_user_id) REFERENCES app_users(user_id)
) ENGINE=InnoDB;

-- Shared RAG knowledge base: policy docs / SOPs / clinical guidelines per
-- department, mixed in with the transactional domain tables at retrieval
-- time by each chatbot's LangGraph retrieval node.
DROP TABLE IF EXISTS knowledge_docs;
CREATE TABLE knowledge_docs (
  doc_id      INT AUTO_INCREMENT PRIMARY KEY,
  dept_id     INT          NOT NULL,
  title       VARCHAR(200) NOT NULL,
  doc_type    VARCHAR(50)  NOT NULL,  -- POLICY | SOP | CLINICAL_GUIDELINE | FAQ | REGULATION
  content     TEXT         NOT NULL,
  tags        VARCHAR(255),
  source      VARCHAR(150),
  is_active   TINYINT(1)   NOT NULL DEFAULT 1,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_docs_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id),
  FULLTEXT KEY ft_docs_content (title, content)
) ENGINE=InnoDB;

-- Daily rollup used by each chatbot's cost/http/llm dashboard so the
-- dashboard doesn't have to aggregate raw log tables on every page load.
DROP TABLE IF EXISTS cost_summary_daily;
CREATE TABLE cost_summary_daily (
  summary_id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  dept_id            INT          NOT NULL,
  chatbot_source     VARCHAR(50)  NOT NULL,
  summary_date       DATE         NOT NULL,
  total_llm_calls    INT          NOT NULL DEFAULT 0,
  total_http_calls   INT          NOT NULL DEFAULT 0,
  total_prompt_tokens INT         NOT NULL DEFAULT 0,
  total_completion_tokens INT     NOT NULL DEFAULT 0,
  total_tokens       BIGINT       NOT NULL DEFAULT 0,
  total_cost_usd     DECIMAL(14,6) NOT NULL DEFAULT 0,
  avg_llm_latency_ms INT          NOT NULL DEFAULT 0,
  avg_http_latency_ms INT         NOT NULL DEFAULT 0,
  error_count        INT          NOT NULL DEFAULT 0,
  UNIQUE KEY uq_summary (dept_id, chatbot_source, summary_date),
  CONSTRAINT fk_summary_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- 2. DEPARTMENT DOMAIN TABLES
-- ---------------------------------------------------------------------

-- CLAIMS -----------------------------------------------------------
DROP TABLE IF EXISTS claims;
CREATE TABLE claims (
  claim_id        INT AUTO_INCREMENT PRIMARY KEY,
  dept_id         INT NOT NULL,
  claim_number    VARCHAR(30) NOT NULL UNIQUE,
  member_id       VARCHAR(20) NOT NULL,
  provider_id     VARCHAR(20) NOT NULL,
  date_of_service DATE NOT NULL,
  cpt_code        VARCHAR(10),
  diagnosis_code  VARCHAR(10),
  billed_amount   DECIMAL(10,2) NOT NULL DEFAULT 0,
  allowed_amount  DECIMAL(10,2) NOT NULL DEFAULT 0,
  paid_amount     DECIMAL(10,2) NOT NULL DEFAULT 0,
  claim_status    VARCHAR(20) NOT NULL,   -- SUBMITTED|IN_REVIEW|APPROVED|DENIED|PAID|APPEALED
  submitted_date  DATE,
  processed_date  DATE,
  denial_reason   VARCHAR(255),
  notes           VARCHAR(500),
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_claims_member (member_id),
  INDEX idx_claims_status (claim_status),
  CONSTRAINT fk_claims_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

-- PRIOR AUTHORIZATION ------------------------------------------------
DROP TABLE IF EXISTS prior_authorizations;
CREATE TABLE prior_authorizations (
  pa_id            INT AUTO_INCREMENT PRIMARY KEY,
  dept_id          INT NOT NULL,
  pa_number        VARCHAR(30) NOT NULL UNIQUE,
  member_id        VARCHAR(20) NOT NULL,
  provider_id      VARCHAR(20) NOT NULL,
  procedure_code   VARCHAR(10),
  diagnosis_code   VARCHAR(10),
  requested_date   DATE NOT NULL,
  urgency          VARCHAR(20) NOT NULL,   -- ROUTINE|URGENT|EMERGENCY
  status           VARCHAR(20) NOT NULL,   -- PENDING|APPROVED|DENIED|PARTIAL|EXPIRED
  decision_date    DATE,
  decision_reason  VARCHAR(255),
  clinical_notes   VARCHAR(500),
  created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_pa_member (member_id),
  INDEX idx_pa_status (status),
  CONSTRAINT fk_pa_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

-- NURSING --------------------------------------------------------------
DROP TABLE IF EXISTS nursing_cases;
CREATE TABLE nursing_cases (
  case_id          INT AUTO_INCREMENT PRIMARY KEY,
  dept_id          INT NOT NULL,
  case_number      VARCHAR(30) NOT NULL UNIQUE,
  member_id        VARCHAR(20) NOT NULL,
  nurse_id         VARCHAR(20) NOT NULL,
  case_type        VARCHAR(50) NOT NULL,   -- CARE_MANAGEMENT|UTILIZATION_REVIEW|DISEASE_MANAGEMENT|DISCHARGE_PLANNING
  acuity_level     VARCHAR(20) NOT NULL,   -- LOW|MEDIUM|HIGH|CRITICAL
  status           VARCHAR(20) NOT NULL,   -- OPEN|IN_PROGRESS|CLOSED
  opened_date      DATE NOT NULL,
  closed_date      DATE,
  care_plan_notes  VARCHAR(500),
  created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_nursing_member (member_id),
  INDEX idx_nursing_status (status),
  CONSTRAINT fk_nursing_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

-- CALL CENTER ------------------------------------------------------------
DROP TABLE IF EXISTS call_center_logs;
CREATE TABLE call_center_logs (
  call_id           INT AUTO_INCREMENT PRIMARY KEY,
  dept_id           INT NOT NULL,
  call_reference    VARCHAR(30) NOT NULL UNIQUE,
  member_id         VARCHAR(20) NOT NULL,
  agent_id          VARCHAR(20) NOT NULL,
  call_datetime     DATETIME NOT NULL,
  call_type         VARCHAR(50) NOT NULL,  -- BENEFITS|CLAIMS_STATUS|COMPLAINT|ENROLLMENT|PROVIDER_SEARCH
  duration_seconds  INT NOT NULL DEFAULT 0,
  resolution_status VARCHAR(20) NOT NULL,  -- RESOLVED|ESCALATED|FOLLOW_UP_NEEDED
  call_notes        VARCHAR(500),
  csat_score        TINYINT,               -- 1-5
  created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_call_member (member_id),
  INDEX idx_call_agent (agent_id),
  CONSTRAINT fk_call_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

-- BILLING ------------------------------------------------------------
DROP TABLE IF EXISTS billing_records;
CREATE TABLE billing_records (
  billing_id      INT AUTO_INCREMENT PRIMARY KEY,
  dept_id         INT NOT NULL,
  invoice_number  VARCHAR(30) NOT NULL UNIQUE,
  member_id       VARCHAR(20) NOT NULL,
  billing_period  VARCHAR(20) NOT NULL,  -- e.g. '2026-07'
  amount_due      DECIMAL(10,2) NOT NULL DEFAULT 0,
  amount_paid     DECIMAL(10,2) NOT NULL DEFAULT 0,
  payment_status  VARCHAR(20) NOT NULL,  -- UNPAID|PARTIAL|PAID|OVERDUE|WRITTEN_OFF
  due_date        DATE NOT NULL,
  paid_date       DATE,
  payment_method  VARCHAR(30),           -- ACH|CREDIT_CARD|CHECK|PAYROLL_DEDUCTION
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_billing_member (member_id),
  INDEX idx_billing_status (payment_status),
  CONSTRAINT fk_billing_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

-- FACILITY & PROVIDERS -------------------------------------------------
DROP TABLE IF EXISTS providers;
CREATE TABLE providers (
  provider_id       INT AUTO_INCREMENT PRIMARY KEY,
  dept_id           INT NOT NULL,
  provider_code     VARCHAR(20) NOT NULL UNIQUE,
  provider_name     VARCHAR(150) NOT NULL,
  npi_number        VARCHAR(15) NOT NULL,
  specialty         VARCHAR(100),
  facility_name     VARCHAR(150),
  network_status    VARCHAR(25) NOT NULL,  -- IN_NETWORK|OUT_OF_NETWORK|PENDING_CREDENTIALING|TERMINATED
  address            VARCHAR(255),
  phone             VARCHAR(30),
  contract_start    DATE,
  contract_end      DATE,
  created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_providers_status (network_status),
  CONSTRAINT fk_providers_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

-- ADJUDICATION -----------------------------------------------------------
DROP TABLE IF EXISTS adjudication_records;
CREATE TABLE adjudication_records (
  adjudication_id  INT AUTO_INCREMENT PRIMARY KEY,
  dept_id          INT NOT NULL,
  claim_number     VARCHAR(30) NOT NULL,
  adjudicator_id   VARCHAR(20) NOT NULL,
  rule_applied     VARCHAR(100) NOT NULL,
  decision         VARCHAR(20) NOT NULL,  -- APPROVE|DENY|ADJUST|PEND
  adjustment_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
  adjudicated_date DATE NOT NULL,
  notes            VARCHAR(500),
  created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_adj_claim (claim_number),
  CONSTRAINT fk_adj_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

-- FINANCE -----------------------------------------------------------------
DROP TABLE IF EXISTS finance_transactions;
CREATE TABLE finance_transactions (
  txn_id        INT AUTO_INCREMENT PRIMARY KEY,
  dept_id       INT NOT NULL,
  txn_reference VARCHAR(30) NOT NULL UNIQUE,
  txn_type      VARCHAR(30) NOT NULL,  -- PREMIUM_RECEIPT|CLAIM_PAYOUT|VENDOR_PAYMENT|ACCRUAL|ADJUSTMENT
  amount        DECIMAL(14,2) NOT NULL,
  currency      CHAR(3) NOT NULL DEFAULT 'USD',
  txn_date      DATE NOT NULL,
  gl_account    VARCHAR(30) NOT NULL,
  description   VARCHAR(255),
  approved_by   VARCHAR(100),
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_finance_type (txn_type),
  CONSTRAINT fk_finance_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

-- MANAGEMENT -----------------------------------------------------------
DROP TABLE IF EXISTS management_reports;
CREATE TABLE management_reports (
  report_id      INT AUTO_INCREMENT PRIMARY KEY,
  dept_id        INT NOT NULL,
  report_ref     VARCHAR(30) NOT NULL UNIQUE,
  report_title   VARCHAR(200) NOT NULL,
  covers_dept_id INT,
  report_period  VARCHAR(20) NOT NULL,  -- e.g. '2026-Q2'
  kpi_summary    JSON,
  prepared_by    VARCHAR(100),
  report_date    DATE NOT NULL,
  created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_mgmt_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id),
  CONSTRAINT fk_mgmt_covers_dept FOREIGN KEY (covers_dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

-- MEMBER SERVICES ------------------------------------------------------
DROP TABLE IF EXISTS member_services_tickets;
CREATE TABLE member_services_tickets (
  ticket_id        INT AUTO_INCREMENT PRIMARY KEY,
  dept_id          INT NOT NULL,
  ticket_number    VARCHAR(30) NOT NULL UNIQUE,
  member_id        VARCHAR(20) NOT NULL,
  agent_id         VARCHAR(20) NOT NULL,
  category         VARCHAR(50) NOT NULL,  -- ID_CARD|ADDRESS_CHANGE|COVERAGE_QUESTION|GRIEVANCE|ENROLLMENT
  priority         VARCHAR(20) NOT NULL,  -- LOW|MEDIUM|HIGH
  status           VARCHAR(20) NOT NULL,  -- OPEN|IN_PROGRESS|RESOLVED|CLOSED
  opened_date      DATE NOT NULL,
  closed_date      DATE,
  resolution_notes VARCHAR(500),
  created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_ticket_member (member_id),
  INDEX idx_ticket_status (status),
  CONSTRAINT fk_ticket_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB;

SET FOREIGN_KEY_CHECKS = 1;
