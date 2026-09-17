import hashlib
import json
from collections.abc import Mapping
from datetime import datetime

from django.utils import timezone

from apps.policy.models import AIPolicy


def details_hash(params: Mapping[str, object]) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def current_policy(at: datetime | None = None) -> AIPolicy | None:
    moment = at or timezone.now()
    return AIPolicy.objects.filter(effective_from__lte=moment).order_by("-effective_from").first()
