"""The company rules that decide what systems a project has."""

from app.services import system_rules

def test_an_edwards_smoke_management_is_the_fire_alarm():
    """The same panel carries it when it is the same make: one system, and
    one material submittal for it. A smoke management system from another
    manufacturer is its own equipment and is not folded in."""
    from types import SimpleNamespace

    def rows(smoke_brand):
        return [
            SimpleNamespace(name="Fire Alarm", brand="EDWARDS"),
            SimpleNamespace(name="Smoke Management", brand=smoke_brand),
        ]

    assert system_rules.smoke_management_integrated(rows("EDWARDS")) is True
    assert system_rules.codes_for_rows(rows("EDWARDS")) == ["FAS"]

    # Another make: not the fire alarm's, so it adds nothing to FAS.
    assert system_rules.smoke_management_integrated(rows("HONEYWELL")) is False
    # Spelling is not the difference; the brand normaliser settles that.
    assert system_rules.smoke_management_integrated(rows("Edwards")) is True
