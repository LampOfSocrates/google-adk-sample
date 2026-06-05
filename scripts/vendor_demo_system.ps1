# Vendor a REAL polyglot + k8s microservices demo as the L0 integration target.
#
# Online Boutique (GoogleCloudPlatform/microservices-demo) is the best match for our
# stack choice: 11 services across Go / Python / Node / Java / C#, real per-service
# Dockerfiles under src/<svc>/, and real k8s manifests under
# kubernetes-manifests/ + release/kubernetes-manifests.yaml. It exercises the messy
# multi-source cases the hand-authored fake_system fixture deliberately avoids.
#
# This does a shallow sparse checkout of ONLY the files L0 cares about (src + manifests),
# not the whole repo. Run from the project root:  ./scripts/vendor_demo_system.ps1

$ErrorActionPreference = "Stop"
$dest = "tests/fixtures/online_boutique"
$repo = "https://github.com/GoogleCloudPlatform/microservices-demo.git"

if (Test-Path $dest) {
    Write-Host "$dest already exists — delete it first to re-vendor." -ForegroundColor Yellow
    exit 0
}

git clone --depth 1 --filter=blob:none --sparse $repo $dest
Push-Location $dest
try {
    # Only the trees the survey parsers read: service source (Dockerfiles, build
    # descriptors, code) and the k8s manifests.
    git sparse-checkout set src kubernetes-manifests release
    Write-Host "Vendored Online Boutique into $dest" -ForegroundColor Green
    Write-Host "Point the L0 survey agent at $dest as the integration target."
}
finally {
    Pop-Location
}
