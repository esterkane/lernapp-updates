from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ExamQuestion(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    section: str = Field(default="Lesen", max_length=120)
    kind: Literal["choice", "text", "writing", "speaking", "open"] = "choice"
    page: int = Field(default=1, ge=1)
    instructions: str = Field(default="", max_length=12000)
    passage: str = Field(default="", max_length=30000)
    question: str = Field(min_length=1, max_length=12000)
    options: list[str] = Field(default_factory=list, max_length=30)
    answers: list[str] = Field(default_factory=list, max_length=30)
    explanation: str = Field(default="", max_length=8000)
    audio_reference: str = Field(default="", max_length=8000)
    points: float = Field(default=1, gt=0, le=100)
    source_pages: list[int] = Field(default_factory=list, max_length=8)
    audio_start: float | None = Field(default=None, ge=0)
    audio_end: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid(self) -> "ExamQuestion":
        if any(p < 1 for p in self.source_pages):
            raise ValueError("Quellseiten müssen positiv sein.")
        if self.kind == "choice" and (len(self.options) < 2 or len(set(self.options)) != len(self.options)):
            raise ValueError("Auswahlfragen brauchen mindestens zwei eindeutige Optionen.")
        if self.kind == "choice" and any(a not in self.options for a in self.answers):
            raise ValueError("Lösungen müssen exakt einer Antwortoption entsprechen.")
        if self.audio_end is not None and self.audio_end <= (self.audio_start or 0):
            raise ValueError("Audio-Ende muss nach dem Anfang liegen.")
        return self


class LearningNote(BaseModel):
    section: int = Field(default=1, ge=1)
    phrase: str = Field(min_length=1, max_length=300)
    meaning: str = Field(min_length=1, max_length=1500)
    usage: str = Field(default="", max_length=1000)
    example: str = Field(default="", max_length=1000)


class ExamDraft(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    level: str = Field(default="B2", max_length=20)
    duration_minutes: int = Field(default=0, ge=0, le=300)
    questions: list[ExamQuestion] = Field(default_factory=list, max_length=200)
    learning_notes: list[LearningNote] = Field(default_factory=list, max_length=240)
    warnings: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique(self) -> "ExamDraft":
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("Fragen brauchen eindeutige IDs.")
        return self
