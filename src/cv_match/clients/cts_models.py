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

    activeStatus: str
    age: int
    educationList: list[EducationItem]
    expectedIndustry: str
    expectedIndustryIds: list[str]
    expectedJobCategory: str
    expectedJobCategoryIds: list[str]
    expectedLocation: str
    expectedLocationIds: list[str]
    expectedSalary: str
    gender: str
    jobState: str
    nowLocation: str
    projectNameAll: list[str]
    workExperienceList: list[WorkExperienceItem]
    workSummariesAll: list[str]
    workYear: int


class Timings(BaseModel):
    model_config = ConfigDict(extra="allow")

    validation: int
    configPreparation: int
    paramsPreparation: int
    apiRequest: int
    dataProcessing: int
    totalTime: int


class CandidateSearchData(BaseModel):
    model_config = ConfigDict(extra="allow")

    candidates: list[Candidate]
    total: int
    page: int | str
    pageSize: int | str


class CandidateSearchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: int
    status: str
    message: str
    data: CandidateSearchData | None
    timings: Timings | None = None


class AuthFailureResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: int
    status: str
    message: str
    data: None = None


class CandidateSearchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    jd: str | None = None
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
    active: int | None = None
    page: int | str = 1
    pageSize: int | str = 10
