"""Demo schema used to prove the LLM layer works (tests, live smoke test, evals).

Real schemas (scoring, tailoring) belong to Phases 5-6.
"""

from pydantic import BaseModel, ConfigDict, Field


class JobExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = Field(description="Hiring company, or empty string if not stated")
    title: str = Field(description="Job title, or empty string if not stated")
    location: str = Field(description="Location, or empty string if not stated")
    skills: list[str] = Field(description="Required skills as stated; empty list if none")
