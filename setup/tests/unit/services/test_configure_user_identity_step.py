from mamemai_setup.domain.plan import InstallationPlan
from mamemai_setup.services.steps import ConfigureUserIdentity

from tests.fakes.fakes import FakeWizardPrompter


class _TextPrompter(FakeWizardPrompter):
    """FakeWizardPrompter's text() always returns its own default — this fixture
    lets a test script real answers for the two text() prompts ConfigureUserIdentity
    asks in order (name, then schema)."""

    def __init__(self, text_answers: list[str]) -> None:
        super().__init__()
        self._text_answers = list(text_answers)

    def text(self, message: str, default: str = "") -> str:
        return self._text_answers.pop(0) if self._text_answers else default


def test_derives_schema_from_the_given_name():
    step = ConfigureUserIdentity()
    prompter = _TextPrompter(["Mary Ann", "mary_ann"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.user_display_name == "Mary Ann"
    assert plan.database_schema == "mary_ann"


def test_blank_name_falls_back_to_user_1():
    step = ConfigureUserIdentity()
    prompter = _TextPrompter(["", "user_1"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.user_display_name == "user-1"
    assert plan.database_schema == "user_1"


def test_schema_prompt_default_is_the_derived_slug():
    step = ConfigureUserIdentity()
    prompter = _TextPrompter(["Alice"])  # schema prompt not scripted -> uses its default
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.database_schema == "alice"


def test_user_can_override_the_derived_schema():
    step = ConfigureUserIdentity()
    prompter = _TextPrompter(["Alice", "family_alice"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.database_schema == "family_alice"


def test_noop_when_schema_already_recorded():
    """Immutable once set — a re-run must not overwrite it or ask again."""
    step = ConfigureUserIdentity()
    prompter = FakeWizardPrompter()  # no answers scripted; would raise if asked
    plan = InstallationPlan(database_schema="alice", from_existing_install=True)

    step.run(plan, prompter)

    assert plan.database_schema == "alice"


def test_existing_install_with_no_recorded_schema_assumes_public():
    """A config predating this feature has its data in "public" already —
    assumed, not prompted, matching the server's own config-loader fallback."""
    step = ConfigureUserIdentity()
    prompter = FakeWizardPrompter()  # no answers scripted; would raise if asked
    plan = InstallationPlan(from_existing_install=True)

    step.run(plan, prompter)

    assert plan.database_schema == "public"
    assert plan.user_display_name is None
