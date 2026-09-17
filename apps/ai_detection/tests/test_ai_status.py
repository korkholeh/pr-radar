from apps.activity.models import AIDisclosure, AIStatus
from apps.ai_detection.models import Confidence
from apps.ai_detection.services import resolve_ai_status


def test_ai_explicit():
    assert resolve_ai_status({Confidence.HIGH}, AIDisclosure.MISSING) == AIStatus.AI_EXPLICIT


def test_ai_disclosed():
    assert resolve_ai_status(set(), AIDisclosure.PARTIAL) == AIStatus.AI_DISCLOSED
    assert resolve_ai_status(set(), AIDisclosure.SUBSTANTIAL) == AIStatus.AI_DISCLOSED


def test_ai_suspected():
    assert resolve_ai_status({Confidence.LOW}, AIDisclosure.MISSING) == AIStatus.AI_SUSPECTED


def test_no_ai():
    assert resolve_ai_status(set(), AIDisclosure.NONE) == AIStatus.NO_AI


def test_unknown():
    assert resolve_ai_status(set(), AIDisclosure.MISSING) == AIStatus.UNKNOWN


def test_high_signal_beats_a_none_disclosure():
    assert resolve_ai_status({Confidence.HIGH}, AIDisclosure.NONE) == AIStatus.AI_EXPLICIT


def test_ambiguous_disclosure_with_no_signals_is_unknown():
    assert resolve_ai_status(set(), AIDisclosure.AMBIGUOUS) == AIStatus.UNKNOWN
