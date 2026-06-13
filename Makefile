HOSTNAME=github.com
NAMESPACE=relaypro-open
NAME=community.dog
BINARY=${NAME}
VERSION=1.1.0
OS_ARCH=linux_amd64

github_release:
	git add -f Makefile
	git add -f galaxy.yml
	git commit -m "release tag: ${VERSION}"
	git tag ${VERSION}
	git push --tags --force

delete_release:
	git tag -d ${VERSION}
	git push --delete origin ${VERSION}
