from llm_evaluator import calculate_candidate_skill_score, prefilter_job


def test_common_state_abbreviations_do_not_trigger_location_filter():
    title = "Software Engineer"
    location = "Noida, India"
    jd = "Experience with ORMs and APIs. Collaborate with teams in CA and OR."
    filtered, passes, reason, _ = prefilter_job(title, location, jd)
    assert filtered is False
    assert passes == 1
    assert reason == ""


def test_explicit_us_location_is_rejected():
    filtered, passes, reason, _ = prefilter_job(
        "Software Engineer", "San Francisco, CA, United States", "Python required"
    )
    assert filtered is True
    assert passes == 0
    assert "Non-India location" in reason


def test_required_experience_is_rejected():
    filtered, passes, reason, years = prefilter_job(
        "Software Engineer", "Noida, India", "Requirements: minimum 5 years of experience."
    )
    assert filtered is True
    assert passes == 0
    assert years == 5


def test_weighted_score_returns_evidence():
    score, analysis = calculate_candidate_skill_score(
        "AI Engineer",
        "Noida, India",
        "Requirements: Python, RAG, FastAPI. Preferred: Docker and AWS.",
    )
    assert score > 50
    assert "required=" in analysis
