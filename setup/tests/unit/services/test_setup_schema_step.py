from mamemai_setup.domain.plan import InstallationPlan
from mamemai_setup.services.steps import SetupSchema

from tests.fakes.fakes import FakeSchemaRunner, FakeWizardPrompter


def test_applies_schema_using_plans_database_url():
    schema_runner = FakeSchemaRunner()
    step = SetupSchema(schema_runner)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert schema_runner.applied_to == [plan.database_url]


def test_falls_back_to_public_schema_when_plan_has_none():
    schema_runner = FakeSchemaRunner()
    step = SetupSchema(schema_runner)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert schema_runner.applied_schemas == ["public"]


def test_uses_plans_recorded_schema_when_set():
    schema_runner = FakeSchemaRunner()
    step = SetupSchema(schema_runner)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan(database_schema="alice")

    step.run(plan, prompter)

    assert schema_runner.applied_schemas == ["alice"]
