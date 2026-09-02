from flask import Blueprint, abort, render_template

from . import content


hub_bp = Blueprint("hub", __name__)


# ============================================================================
# HOME
# ============================================================================

@hub_bp.get("/")
def home():
    return render_template("home.html")


# ============================================================================
# ARCHITECTURE
# ============================================================================

@hub_bp.get("/architecture")
def architecture():
    return render_template("architecture.html")


# ============================================================================
# HIPAA COMPLIANCE
# ============================================================================

@hub_bp.get("/hipaa-compliance")
def hipaa_compliance():
    return render_template(
        "hipaa.html",
        gpu_pricing=content.GPU_PRICING,
    )


# ============================================================================
# TRAINING INDEX
# ============================================================================

@hub_bp.get("/training")
def training_index():
    categories = sorted(
        {
            topic["category"]
            for topic in content.TRAINING_TOPICS
        }
    )

    return render_template(
        "training_index.html",
        categories=categories,
    )


# ============================================================================
# TRAINING TOPIC
# ============================================================================

@hub_bp.get("/training/<slug>")
def training_topic(slug: str):
    topic = next(
        (
            topic
            for topic in content.TRAINING_TOPICS
            if topic["slug"] == slug
        ),
        None,
    )

    if topic is None:
        abort(404)

    return render_template(
        "training_topic.html",
        topic=topic,
    )