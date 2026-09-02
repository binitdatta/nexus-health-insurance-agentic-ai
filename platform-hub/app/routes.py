from flask import Blueprint, abort, render_template

from . import content

hub_bp = Blueprint("hub", __name__)


@hub_bp.get("/")
def home():
    return render_template("home.html")


@hub_bp.get("/architecture")
def architecture():
    return render_template("architecture.html")


@hub_bp.get("/hipaa-compliance")
def hipaa_compliance():
    return render_template("hipaa.html", gpu_pricing=content.GPU_PRICING)


@hub_bp.get("/training")
def training_index():
    categories = sorted({t["category"] for t in content.TRAINING_TOPICS})
    return render_template("training_index.html", categories=categories)


@hub_bp.get("/training/<slug>")
def training_topic(slug: str):
    topic = next((t for t in content.TRAINING_TOPICS if t["slug"] == slug), None)
    if topic is None:
        abort(404)
    return render_template("training_topic.html", topic=topic)
