HOSTNAME=github.com
NAMESPACE=relaypro-open
NAME=community.dog
BINARY=${NAME}
VERSION=1.0.5
OS_ARCH=linux_amd64

github_release:
	sed -i 's/^version: .*$$/version: ${VERSION}/g' galaxy.yml
	git add -f galaxy.yml
	git commit -m "release tag: ${VERSION}"
	git tag ${VERSION}
	git push --tags --force

delete_release:
	git tag -d ${VERSION}
	git push --delete origin ${VERSION}
