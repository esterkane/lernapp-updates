from lernapp_ui.library import library_items, unique_materials


def test_exact_copies_merge_but_distinct_versions_with_same_title_remain():
    items = [
        {"id": "draft", "title": "B2", "sha256": "one", "reviewed": False},
        {"id": "ready", "title": "B2", "sha256": "one", "reviewed": True},
        {"id": "edition", "title": "B2", "sha256": "two"},
    ]
    assert [item["id"] for item in unique_materials(items)] == ["ready", "edition"]


def test_indexed_shadow_is_hidden_but_unlinked_document_is_kept():
    exams = [{"id": "pdf", "title": "B2", "rag_document_id": "shadow"}]
    docs = [{"id": "shadow", "title": "B2"}, {"id": "own", "title": "My notes"}]
    assert [item["id"] for item in library_items(exams, docs)] == ["pdf", "own"]
