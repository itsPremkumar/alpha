"""Tests for Adversarial Hyperplan Multi-Reviewer Pipeline."""

from alpha.planning.hyperplan import HyperplanPipeline


def test_hyperplan_approves_comprehensive_plan():
    pipeline = HyperplanPipeline()
    good_plan = """
    # Feature: Add Redis Caching Layer
    
    ## Prerequisites & Dependencies
    - Redis server running on localhost:6379
    - aioredis package installed
    
    ## Architecture
    - Modular CacheService interface
    - Graceful fallback to database when cache misses
    
    ## Security
    - All cache keys hashed with project prefix
    - No plaintext secrets stored in cache
    
    ## Verification Plan & Automated Tests
    - Run pytest tests/test_cache.py -v
    - Capture performance benchmarks before and after
    """
    report = pipeline.review_plan("Redis Caching", good_plan)
    assert report.overall_status == "APPROVED"
    assert len(report.verdicts) == 4
    assert all(v.status == "APPROVED" for v in report.verdicts)


def test_hyperplan_blocks_unverified_and_dangerous_plan():
    pipeline = HyperplanPipeline()
    bad_plan = """
    # Clean Database Plan
    Just run `sudo rm -rf /var/data` and reset the server.
    No need for tests, it should just work.
    """
    report = pipeline.review_plan("Dangerous Cleanup", bad_plan)
    assert report.overall_status == "BLOCKED"
    
    # Security reviewer rejects
    sec_verdict = next(v for v in report.verdicts if "Security" in v.reviewer_role)
    assert sec_verdict.status == "REJECTED"

    # QA executor rejects
    qa_verdict = next(v for v in report.verdicts if "QA" in v.reviewer_role)
    assert qa_verdict.status == "REJECTED"
