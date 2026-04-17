from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EducationItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    degree: str | None = None
    education: str | None = None
    educationCode: str | None = None
    endTime: str | None = None
    school: str | None = None
    schoolTags: list[Any] = Field(default_factory=list)
    sortNum: int | None = None
    speciality: str | None = None
    startTime: str | None = None
    unified: str | None = None


class WorkExperienceItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    categoryIdLevel1: str | None = None
    categoryIdLevel2: str | None = None
    categoryIdLevel3: str | None = None
    categoryIdsAll: list[str] = Field(default_factory=list)
    company: str | None = None
    createTime: int | None = None
    duration: str | None = None
    endTime: str | None = None
    groupCompanyIds: list[str] = Field(default_factory=list)
    industryIdLevel1: str | None = None
    industryIdLevel2: str | None = None
    industryIdsAll: list[str] = Field(default_factory=list)
    level: int | None = None
    sortNum: int | None = None
    startTime: str | None = None
    summary: str | None = None
    tagNames: list[Any] = Field(default_factory=list)
    title: str | None = None
    updateTime: int | None = None
    years: list[int] = Field(default_factory=list)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="allow")

    activeStatus: str | None = None
    age: int | None = None
    educationList: list[EducationItem] = Field(default_factory=list)
    expectedIndustry: str | None = None
    expectedIndustryIds: list[str] = Field(default_factory=list)
    expectedJobCategory: str | None = None
    expectedJobCategoryIds: list[str] = Field(default_factory=list)
    expectedLocation: str | None = None
    expectedLocationIds: list[str] = Field(default_factory=list)
    expectedSalary: str | None = None
    gender: str | None = None
    jobState: str | None = None
    nowLocation: str | None = None
    projectNameAll: list[str] = Field(default_factory=list)
    workExperienceList: list[WorkExperienceItem] = Field(default_factory=list)
    workSummariesAll: list[str] = Field(default_factory=list)
    workYear: int | None = None


class CandidateSearchData(BaseModel):
    model_config = ConfigDict(extra="allow")

    candidates: list[Candidate]
    total: int


class CandidateSearchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: int
    status: str
    message: str
    data: CandidateSearchData | None


class CandidateSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str | None = None
    school: str | None = None
    company: str | None = None
    position: str | None = None
    workContent: str | None = None
    location: str | list[str] | None = None
    degree: int | None = None
    schoolType: int | None = None
    workExperienceRange: int | None = None
    gender: int | None = None
    age: int | None = None
    page: int | str = 1
    pageSize: int | str = 10
