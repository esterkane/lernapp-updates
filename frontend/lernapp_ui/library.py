"""Library identity rules: exact file copies merge; titles alone never imply identity."""


def unique_materials(items):
    selected = {}
    for item in items:
        identity = (item.get("sha256") or item["id"], item.get("audio_sha256"))
        previous = selected.get(identity)
        if previous is None or (bool(item.get("reviewed")), item.get("question_count", 0)) > (
            bool(previous.get("reviewed")), previous.get("question_count", 0)
        ):
            selected[identity] = item
    return list(selected.values())


def library_items(exams, documents):
    linked = {str(item["rag_document_id"]) for item in exams if item.get("rag_document_id")}
    originals = [{**item, "entry_type": "exam"} for item in unique_materials(exams)]
    extras = [{**item, "entry_type": "document"} for item in documents if str(item["id"]) not in linked]
    return sorted(originals + extras, key=lambda item: item.get("title", "").casefold())
