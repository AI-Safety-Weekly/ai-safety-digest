"""Local integration guards for required intake plus explicit source health."""
import pytest
from safety_digest import lab_collector
from safety_digest.primary_reports import PrimaryReportCollectionError


@pytest.mark.parametrize('incomplete',[False,True])
def test_required_failure_records_health_and_cannot_be_swallowed(monkeypatch,incomplete):
    error=(lab_collector.CollectionIncomplete([],2) if incomplete else
           PrimaryReportCollectionError('source unavailable'))
    def fail(*args):raise error
    monkeypatch.setattr(lab_collector,'_collect_one',fail)
    health=[]
    with pytest.raises(RuntimeError,match='Required primary-report source'):
        lab_collector.collect([{'name':'required','required':True}],health=health)
    assert health[0]['status']==('incomplete' if incomplete else 'failed')
    if incomplete:assert health[0]['failed_retrievals']==2
