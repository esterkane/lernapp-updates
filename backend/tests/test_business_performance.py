from app.services import learner_profile, tutor


def test_business_goal_is_editable_and_workspace_owned(client):
    a = client.post("/workspaces", json={"name": "Finance"}).json()["id"]
    b = client.post("/workspaces", json={"name": "Other"}).json()["id"]
    goal = "Collections Manager: Zahlungsvereinbarungen freundlich und verbindlich besprechen."
    assert (
        client.patch(f"/learners/{a}", json={"learning_goal": goal}, headers={"X-Lernapp-Workspace": a}).status_code
        == 200
    )
    assert goal in learner_profile.profile_block(a)
    assert goal not in learner_profile.profile_block(b)
    assert (
        client.patch(f"/learners/{a}", json={"learning_goal": "wrong"}, headers={"X-Lernapp-Workspace": b}).status_code
        == 403
    )
    assert client.patch(f"/learners/{a}", json={"learning_goal": ""}).status_code == 200
    assert goal not in learner_profile.profile_block(a)


def test_history_budget_preserves_recent_messages():
    history = [{"role": "user", "content": "a" * 10000}, {"role": "assistant", "content": "recent"}]
    result = tutor.bounded_history(history)
    assert sum(len(m["content"]) for m in result) <= tutor.HISTORY_CHAR_BUDGET
    assert result[-1]["content"] == "recent"
    assert history[0]["content"] == "a" * 10000


def test_turn_reports_nonnegative_timings(client):
    r = client.post("/sessions", json={"kind": "tutor"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    result = client.post(f"/sessions/{sid}/turn", json={"text": "Wie vereinbare ich einen Zahlungstermin?"})
    assert result.status_code == 200, result.text
    times = result.json()["timings_ms"]
    assert times["total"] >= times["reply_and_context"] >= 0
    assert times["speech_input"] >= 0 and times["voice_and_tip"] >= 0
    assert times["retrieval"] >= times["query_embedding"] >= 0
    assert times["retrieval"] >= times["retrieval_database"] >= 0
    assert times["reply_and_context"] >= times["retrieval"] + times["answer_generation"]
    stored = client.get(f"/sessions/{sid}").json()["turns"][-1]["meta"]["timings_ms"]
    assert stored == times
