#!/bin/sh

set -e

builds=build/
packages=${builds}*.deb
folder=`mktemp -u tmp.XXXXXXXXXXXXXXX`
aptly_user="$APTLY_USER"
aptly_password="$APTLY_PASSWORD"
aptly_api="https://aptly-ops.aptly.info"
version=`make version`

action=$1
dist=$2

usage() {
    echo "Usage: $0 nighly jammy|focal|bookworm" >&2
    echo "       $0 release" >&2
}

# repos and publish must be created before using this script:
#!/bin/sh
#
#for dist in buster bullseye bookworm focal jammy
#do
#    aptly repo create -distribution=$dist -component=main aptly-ci-$dist
#    aptly publish repo -multi-dist -architectures="amd64,i386,arm64,armhf" -acquire-by-hash \
#                       -component=main -distribution=$dist -batch -keyring=aptly.pub aptly-ci-$dist \
#                       s3:repo.aptly.info:ci
#done



if [ -z "$action" ]; then
    usage
    exit 1
fi

if [ "action" = "ci" ] && [ -z "$dist" ]; then
    usage
    exit 1
fi

echo "Publishing version '$version' to $action for $dist...\n"

upload()
{
    echo "\nUploading files:"
    for file in $packages; do
        echo " - $file"
        curl -fsS -X POST -F "file=@$file" -u $aptly_user:$aptly_password ${aptly_api}/api/files/$folder
    done
    echo
}

cleanup() {
    echo "\nCleanup..."
    curl -fsS -X DELETE  -u $aptly_user:$aptly_password ${aptly_api}/api/files/$folder
    echo
}
trap cleanup EXIT

update_publish() {
    _publish=$1
    _dist=$2
    jsonret=`curl -fsS -X PUT -H 'Content-Type: application/json' --data \
        '{"AcquireByHash": true, "MultiDist": true,
          "Signing": {"Batch": true, "Keyring": "aptly.repo/aptly.pub", "secretKeyring": "aptly.repo/aptly.sec", "PassphraseFile": "aptly.repo/passphrase"}}' \
        -u $aptly_user:$aptly_password ${aptly_api}/api/publish/$_publish/$_dist?_async=true`
    _task_id=`echo $jsonret | jq .ID`
    for t in `seq 180`
    do
        jsonret=`curl -fsS -u $aptly_user:$aptly_password ${aptly_api}/api/tasks/$_task_id`
        _state=`echo $jsonret | jq .State`
        if [ "$_state" = "2" ]; then
            break
        fi
        sleep 1
    done
}

if [ "$action" = "ci" ]; then
    if echo "$version" | grep -vq "+"; then
       # skip ci when on release tag
       exit 0
    fi

    aptly_repository=aptly-ci-$dist
    aptly_published=s3:repo.aptly.info:ci

elif [ "$action" = "release" ]; then
    aptly_repository=aptly-release-$dist
    aptly_published=s3:repo.aptly.info:release
fi

upload

echo "\nAdding packages to $aptly_repository ..."
curl -fsS -X POST -u $aptly_user:$aptly_password ${aptly_api}/api/repos/$aptly_repository/file/$folder
echo

echo "\nUpdating published repo $aptly_published ..."
update_publish $aptly_published $dist
echo

# if [ "$action" = "OBSOLETErelease" ]; then
#     aptly_repository=aptly-release
#     aptly_snapshot=aptly-$version
#     aptly_published=s3:repo.aptly.info:./squeeze
#
#     echo "\nAdding packages to $aptly_repository..."
#     curl -fsS -X POST -u $aptly_user:$aptly_password ${aptly_api}/api/repos/$aptly_repository/file/$folder
#     echo
#
#     echo "\nCreating snapshot $aptly_snapshot from repo $aptly_repository..."
#     curl -fsS -X POST -u $aptly_user:$aptly_password -H 'Content-Type: application/json' --data \
#         "{\"Name\":\"$aptly_snapshot\"}" ${aptly_api}/api/repos/$aptly_repository/snapshots
#     echo
#
#     echo "\nSwitching published repo $aptly_published to use snapshot $aptly_snapshot..."
#     curl -fsS -X PUT -H 'Content-Type: application/json' --data \
#         "{\"AcquireByHash\": true, \"Snapshots\": [{\"Component\": \"main\", \"Name\": \"$aptly_snapshot\"}],
#                                    \"Signing\": {\"Batch\": true, \"Keyring\": \"aptly.repo/aptly.pub\",
#                                                  \"secretKeyring\": \"aptly.repo/aptly.sec\", \"PassphraseFile\": \"aptly.repo/passphrase\"}}" \
#         -u $aptly_user:$aptly_password ${aptly_api}/api/publish/$aptly_published
#     echo
# fi

