from mamemai_server.services.session import _strip_markdown


def test_strips_emphasis_and_code_markers():
    """Spec: FR-106, TR-308"""
    assert _strip_markdown("This is **bold** and _italic_ and `code`.") == "This is bold and italic and code."


def test_strips_headers():
    """Spec: FR-106, TR-308"""
    assert _strip_markdown("### Updated Profile Brief") == "Updated Profile Brief"


def test_strips_horizontal_rule_line():
    """Spec: FR-106, TR-308"""
    assert _strip_markdown("Before\n---\nAfter") == "Before\nAfter"


def test_strips_emoji():
    """Spec: FR-106, TR-308"""
    assert _strip_markdown("Great job! 🎉 Keep going 💾") == "Great job!  Keep going "


def test_real_world_gemma_response():
    """Spec: FR-106, TR-308"""
    # Header marker + its adjoining space are stripped; the space between the emoji and
    # the following word is untouched (only the emoji itself is removed), leaving one
    # leading space — harmless for TTS, unlike the un-stripped "###"/emoji themselves.
    assert _strip_markdown("### 💾 Updated Profile Brief") == " Updated Profile Brief"


def test_strips_cast_role_markers():
    """Spec: FR-106, TR-308 — cast-role labels the model invents are never spoken.

    Observed live 2026-07-18 with the two-teacher cast. Vestigial since TR-305 moved
    voice selection to per-segment language detection: nothing consumes them, and the
    persona prompt asks for no markers at all — they were still synthesized literally.
    """
    assert _strip_markdown("[SPEAKER:default] Ciao!") == "Ciao!"
    assert _strip_markdown("[TARGETTEACHER] Buongiorno.") == "Buongiorno."
    assert _strip_markdown("Allora [GUIDE] ripetiamo.") == "Allora ripetiamo."


def test_strips_localized_control_tag():
    """Spec: FR-106, TR-308 — a translated control token is still never spoken.

    The tutor emitted `[Focalizza: ...]` for `[FOCUS:]` (2026-07-13): a multilingual
    model translating its own control token, which TR-304's literal `[FOCUS:` matcher
    can never catch. It is still lost as a signal — but no longer spoken.
    """
    assert _strip_markdown("[Focalizza: pratica orale] Va bene.") == "Va bene."


def test_leaves_ordinary_bracketed_speech_alone():
    """Spec: FR-106, TR-308 — the rule targets marker shape, not every bracket.

    A single unspaced token (optionally with a `:` payload) is a marker; a bracketed
    phrase or list is ordinary content and must survive, since stripping it would
    silently drop words the user is meant to hear.
    """
    assert _strip_markdown("The array is [1, 2, 3] okay.") == "The array is [1, 2, 3] okay."
    assert _strip_markdown("See the note [see below] please.") == "See the note [see below] please."
