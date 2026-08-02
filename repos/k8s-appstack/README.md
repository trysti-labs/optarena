# k8s-appstack

A small multi-manifest Kubernetes application deployment: Namespace,
ConfigMap, Deployment, Service, HorizontalPodAutoscaler, and
PodDisruptionBudget for a fictional "webstore-api" app, all under `k8s/`.

There's no real cluster here - cases are checked with `python3 check_X.py`
scripts that `yaml.safe_load` the relevant manifests and assert structural /
cross-file consistency (e.g. a Service's `targetPort` matching the
Deployment's `containerPort`).
