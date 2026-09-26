"""One-command audited causal study; blocked studies still produce a report."""

from .causal import import_panel, audit_panel, estimate_effect, build_causal_report
from .contracts import require


def run_causal_workflow(store, document, protocol, seed=2045, replicates=1000):
    require(type(seed) is int and type(replicates) is int and 100 <= replicates <= 10000, 'INVALID_BOOTSTRAP_CONFIG')
    panel_id = import_panel(store,document)
    audit_id = audit_panel(store,panel_id,protocol)
    audit = store.get(audit_id,'causal_audit')['payload']
    effect_id = None
    inference_status = 'not_estimated'
    status = 'blocked'
    if audit['status'] == 'ready_under_assumptions':
        effect_id = estimate_effect(store,audit_id,seed,replicates)
        effect = store.get(effect_id,'causal_effect')['payload']
        inference_status = effect['inference_status']
        status = 'estimated_under_assumptions' if effect['ci95'] is not None else 'estimated_without_inference'
    report = build_causal_report(store,effect_id or audit_id)
    inputs = [panel_id,audit_id,report['artifact_id']] + ([effect_id] if effect_id else [])
    summary = dict(panel=panel_id,audit=audit_id,effect=effect_id,report=report['artifact_id'],
                   analysis_status=status,inference_status=inference_status,blockers=audit['blockers'],
                   synthetic=audit['synthetic'],seed=seed,replicates=replicates)
    run_id = store.put('causal_run',summary,inputs,dict(seed=seed,replicates=replicates))
    return dict(summary,run=run_id,report=report)
