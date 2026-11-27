from pydantic import BaseModel

from app.rules import load_template
from app.services.visuals import _headroom, plural


def test_headroom_texts():
    sc = load_template("finance_notion5").block_by_id(1).scoring   # ≤0 → 100%, ≤3 → 50%, else 0
    assert _headroom(sc, 0) == "Следующее нарушение снизит блок до 50%"
    assert _headroom(sc, 1) == "Запас: 2 нарушения до снижения до 0%"
    assert _headroom(sc, 3) == "Следующее нарушение снизит блок до 0%"
    assert _headroom(sc, 4) == "Лимит нарушений исчерпан"


def test_plural():
    forms = ("нарушение", "нарушения", "нарушений")
    assert [plural(n, *forms) for n in (1, 2, 5, 11, 21, 22)] == \
           ["нарушение", "нарушения", "нарушений", "нарушений", "нарушение", "нарушения"]


def test_call_json_repairs_bad_answer(monkeypatch):
    from app.llm import client   # needs .env with SECRET_KEY
    answers = iter(["не JSON", '```json\n{"x": 1}\n```'])
    monkeypatch.setattr(client, "call", lambda msgs, model=None, **kw: next(answers))

    class S(BaseModel):
        x: int

    assert client.call_json([{"role": "user", "content": "hi"}], S).x == 1
