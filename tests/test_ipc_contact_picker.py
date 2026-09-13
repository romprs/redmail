from __future__ import annotations

from redmail.ipc_server import handle_request


class PickerController:
    """Подставное окно: адресная книга на экране для формы встречи — по
    протоколу помощника (contact_picker_open/select/state/accept/cancel)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.state = {"query": "", "candidates": [
            {"number": 1, "name": "Шилкин Александр", "email": "a@x.ru", "checked": False},
            {"number": 2, "name": "Шилкин Евгений", "email": "e@x.ru", "checked": False},
        ]}

    def ipc_contact_picker_open(self, query=""):
        self.calls.append(("open", {"query": query}))
        return dict(self.state, query=query)

    def ipc_contact_picker_select(self, *, number=None, query=None, all_visible=False, checked=True):
        self.calls.append(("select", {"number": number, "query": query, "all": all_visible, "checked": checked}))
        return dict(self.state, touched=1)

    def ipc_contact_picker_state(self):
        return self.state

    def ipc_contact_picker_accept(self):
        self.calls.append(("accept", {}))
        return [{"name": "Шилкин Евгений", "email": "e@x.ru"}]

    def ipc_contact_picker_cancel(self):
        self.calls.append(("cancel", {}))


def test_contact_picker_open_returns_numbered_candidates() -> None:
    controller = PickerController()
    response = handle_request(controller, {"action": "contact_picker_open", "args": {"query": "шилкин"}})
    assert response["ok"] and response["opened"] == "contact_picker"
    assert response["picker"]["query"] == "шилкин"
    assert [c["number"] for c in response["picker"]["candidates"]] == [1, 2]
    assert controller.calls == [("open", {"query": "шилкин"})]


def test_contact_picker_select_by_number_query_and_all() -> None:
    controller = PickerController()
    assert handle_request(controller, {"action": "contact_picker_select", "args": {"number": 2}})["picker"]["touched"] == 1
    handle_request(controller, {"action": "contact_picker_select", "args": {"query": "евгений", "checked": False}})
    handle_request(controller, {"action": "contact_picker_select", "args": {"all": True}})
    assert controller.calls == [
        ("select", {"number": 2, "query": None, "all": False, "checked": True}),
        ("select", {"number": None, "query": "евгений", "all": False, "checked": False}),
        ("select", {"number": None, "query": None, "all": True, "checked": True}),
    ]


def test_contact_picker_select_validates_arguments() -> None:
    controller = PickerController()
    assert handle_request(controller, {"action": "contact_picker_select", "args": {}})["ok"] is False
    assert handle_request(controller, {"action": "contact_picker_select", "args": {"number": 0}})["ok"] is False
    assert handle_request(controller, {"action": "contact_picker_select", "args": {"number": "2"}})["ok"] is False
    assert handle_request(controller, {"action": "contact_picker_select", "args": {"number": 1, "checked": "да"}})["ok"] is False
    assert controller.calls == []


def test_contact_picker_state_accept_cancel() -> None:
    controller = PickerController()
    assert handle_request(controller, {"action": "contact_picker_state"})["picker"]["candidates"][1]["name"] == "Шилкин Евгений"
    accepted = handle_request(controller, {"action": "contact_picker_accept"})
    assert accepted["accepted"] is True and accepted["selected"] == [{"name": "Шилкин Евгений", "email": "e@x.ru"}]
    assert handle_request(controller, {"action": "contact_picker_cancel"}) == {"ok": True, "cancelled": True}
    assert [c[0] for c in controller.calls] == ["accept", "cancel"]


def test_contact_picker_errors_are_reported_not_raised() -> None:
    class Closed(PickerController):
        def ipc_contact_picker_state(self):
            raise RuntimeError("Адресная книга не открыта.")

    response = handle_request(Closed(), {"action": "contact_picker_state"})
    assert response["ok"] is False and "не открыта" in response["error"]
