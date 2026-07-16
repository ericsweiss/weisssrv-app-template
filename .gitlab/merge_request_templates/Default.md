<!--
Default merge-request template. Keep the Testing section as prose describing
what you actually ran — not a checklist of intentions.
-->

## Summary

<!-- One or two sentences: what this MR does and why. -->

## Changes

<!-- Bullet the notable changes. Group by area (workload / routing / CI / docs). -->

-

## Testing done

<!--
Describe the verification you performed, in prose. For example:
"Ran `task lint` clean; `kustomize build kubernetes/flux | kubeconform`
validated 12 resources; deployed to a scratch namespace and confirmed the
readiness probe passes and the public route serves TLS."
-->

## Deploy notes

<!--
Anything the operator or reviewer must know: new secrets to create, a weisssrv
wiring change needed (new tenant / new internal DNS rewrite), an Authentik
provider to set up, image-tag bumps, or rollback considerations. "None" is a
valid answer.
-->

/label ~changeme-app
