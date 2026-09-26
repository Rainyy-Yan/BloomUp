"""Offline, timed synthetic reproduction with honest external-evidence limits."""

from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

from .allocation import allocation_scenarios
from .causal_demo import run_causal_demo
from .contracts import file_hash
from .demo import run_demo
from .pipeline import write_csv, write_json
from .redteam import run_attacks
from .storage import implementation_hash
from .student_export import export_students


def run_reproduction(store, include_tests=True):
    started=time.perf_counter()
    project=Path(__file__).resolve().parents[1]
    checks=[]
    if include_tests:
        tests=subprocess.run([sys.executable,'-m','unittest','discover','-v'],cwd=project,
                             capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=300)
        log=store.root/'reproduction-tests.txt'
        log.write_text(tests.stdout+'\n'+tests.stderr,encoding='utf-8')
        checks.append(dict(name='unittest',exit_code=tests.returncode,log=log.name,sha256=file_hash(log)))
    demo=run_demo(store.root, 'aiv-v2', use_runner=True)
    store.verify_tree(demo['report']['artifact_id'])
    export=export_students(store,demo['metrics'],enabled=True)
    causal=run_causal_demo(store)
    for study in causal['studies']:store.verify_tree(study['run'])
    redteam=run_attacks()
    red_id=store.put('red_team',redteam)
    allocation_id=store.put('allocation_demo',dict(synthetic=True,scenarios=allocation_scenarios()))
    output=store.root/'reproduction_aggregate'
    output.mkdir(exist_ok=True)
    red_rows=[dict(r,observed_change=json.dumps(r['observed_change'],ensure_ascii=False)) for r in redteam['rows']]
    write_csv(output/'red_team_results.csv',red_rows,list(red_rows[0]))
    observed=store.get(demo['observed'])['payload']['rows']
    write_csv(output/'synthetic_observed_summary.csv',observed,list(observed[0]))
    schemes=store.get(demo['sensitivity'])['payload']['rows']
    # Nested configs remain in verified JSON artifacts; CSV is an aggregate viewing aid.
    write_csv(output/'synthetic_aiv_schemes.csv',schemes,
              ['term','scenario','students','excluded_students','mean_aiv','rank_correlation_with_primary','max_absolute_score_change'])
    elapsed=time.perf_counter()-started
    result=dict(status='completed_with_known_limits' if all(c['exit_code']==0 for c in checks) else 'tests_failed',
                generated_at=datetime.now(timezone.utc).isoformat(),synthetic=True,code_hash=implementation_hash(),
                environment=dict(python=platform.python_version(),system=platform.system(),
                                 dependencies={name:importlib.metadata.version(name) for name in ('openpyxl','et-xmlfile')},
                                 requirements_sha256=file_hash(project/'requirements.txt')),
                elapsed_seconds=elapsed,within_30_minutes_on_this_machine=elapsed<=1800,
                third_party_reproduction=False,real_research_reproduced=False,network_calls=0,
                checks=checks,tests_executed=include_tests,workflow_report=demo['report']['artifact_id'],
                inference=demo['inference']['run_artifact'],synthetic_student_export=export['artifact_id'],
                causal_runs=[s['run'] for s in causal['studies']],red_team=red_id,
                red_team_status_counts=redteam['status_counts'],allocation=allocation_id,
                blockers=['Independent human labels and measured review costs are unavailable.',
                          'Real causal outcomes and treatment/comparison design are not established.',
                          'Real q and Agent opportunity catalog are unconfirmed.',
                          'Historical label/attack claims are not independently reproduced.',
                          'External reviewer timing and official team/submission details remain pending.'])
    inputs=[demo['report']['artifact_id'],demo['inference']['run_artifact'],export['artifact_id'],
            red_id,allocation_id]+result['causal_runs']
    artifact=store.put('reproduction',result,inputs)
    store.verify_tree(artifact)
    result['artifact_id']=artifact
    write_json(store.root/'reproduction-result.json',result)
    return result
