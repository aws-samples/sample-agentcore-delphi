"""Router training, artifacts and publication (design §4 Phase 2; ADR-008).

Most of this package is offline, but not all of it: the serving container imports the
artifact round-trip at cold start (`delphi.agent.app.build_agent_from_env` ->
`artifact_store.load_learned_router`), which pulls in the modules named below. They are
shared between training and serving, must stay request-path-safe (standard library,
numpy and delphi.* only — never torch, scikit-learn or another heavy ML dependency), and
this line is checked against the static import closure of delphi.agent.entrypoint
(T-PKG-U3-5):

Request-path-safe modules: delphi.train.artifact_store, delphi.train.harness, delphi.train.routers

Everything else here is offline-only and never enters the serving closure:
`harness.train_and_select` and the training entry points, `generate` (the artifact
generator), `publish` (the `delphi-artifact` CLI), `artifact_contract` (the C3 checker) and
`artifact_location` (the publisher's destinations).
"""
