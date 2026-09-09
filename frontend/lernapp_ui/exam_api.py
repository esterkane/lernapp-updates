"""HTTP-only model-test client."""

from typing import Any

from lernapp_ui import api


def upload(pdf, audio, title):
    files = {"file": (pdf.name, pdf.getvalue(), "application/pdf")}
    if audio:
        files["audio"] = (audio.name, audio.getvalue(), audio.type)
    return api._request("POST", "/exams", files=files, data={"title": title}, timeout=180)


def listing():
    return api._get("/exams")


def history():
    return api._get("/exams/attempts")


def sources():
    return api._get("/exams/sources")


def import_source(key):
    return api._post(f"/exams/sources/{key}", timeout=240)


def editor(exam_id):
    return api._get(f"/exams/{exam_id}/editor")


def save(exam_id, draft, reviewed, revision):
    return api._put(f"/exams/{exam_id}", {"draft": draft, "reviewed": reviewed, "revision": revision})


def extract(exam_id, first, last):
    return api._post(f"/exams/{exam_id}/extract", {"first": first, "last": last}, timeout=240)


def asset(exam_id, kind):
    return api._request("GET", f"/exams/{exam_id}/assets/{kind}", raw=True, timeout=120)


def start(exam_id, timed):
    return api._post(f"/exams/{exam_id}/start", {"timed": timed})


def attempt(attempt_id):
    return api._get(f"/exams/attempts/{attempt_id}")


def submit(attempt_id, answers):
    return api._post(f"/exams/attempts/{attempt_id}/submit", {"answers": answers})


def delete(exam_id):
    return api._delete(f"/exams/{exam_id}")


def attach_audio(exam_id, audio):
    return api._request(
        "POST", f"/exams/{exam_id}/audio", files={"file": (audio.name, audio.getvalue(), audio.type)}, timeout=180
    )


def audio_suggestions(exam_id):
    return api._post(f"/exams/{exam_id}/audio-suggestions", timeout=600)


def extract_all(exam_id, progress):
    """Process every page in bounded requests; committed page coverage survives retries."""
    data = editor(exam_id)
    total = len(data["pages"])
    completed = set(data.get("extracted_pages", []))
    progress(len(completed), total)
    first = 1
    while first <= total:
        if first in completed:
            first += 1
            continue
        last = first
        while last < min(first + 3, total) and last + 1 not in completed:
            last += 1
        data = extract(exam_id, first, last)
        completed = set(data.get("extracted_pages", []))
        progress(len(completed), total)
        first = last + 1
    return data


def start_import(exam_id):
    return api._post(f"/exams/{exam_id}/import-job")


def import_status(exam_id):
    return api._get(f"/exams/{exam_id}/import-job")


def page_image(exam_id, page, attempt_id=None):
    return api._request("GET", f"/exams/{exam_id}/pages/{page}", params={"attempt_id": attempt_id}, raw=True, timeout=60)


def save_progress(attempt_id: str, answers: dict[str, str], position: int, revision: int) -> dict[str, Any]:
    return api._put(f"/exams/attempts/{attempt_id}/progress", {"answers": answers, "position": position, "revision": revision})


def reset_progress(attempt_id: str, revision: int) -> dict[str, Any]:
    return api._post(f"/exams/attempts/{attempt_id}/reset", {"revision": revision})


def check_answer(attempt_id, question_id, answer, revision):
    return api._post(f"/exams/attempts/{attempt_id}/questions/{question_id}/check", {"answer": answer, "revision": revision})


def question_audio(attempt_id, question_id):
    return api._request("GET", f"/exams/attempts/{attempt_id}/questions/{question_id}/audio", raw=True, timeout=120)


def upload_media(transcript, audio, title):
    return api._request("POST", "/exams/media", files={
        "transcript": (transcript.name, transcript.getvalue(), "text/vtt"),
        "audio": (audio.name, audio.getvalue(), audio.type),
    }, data={"title": title}, timeout=180)
