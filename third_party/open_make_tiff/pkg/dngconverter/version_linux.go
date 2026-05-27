//go:build linux

package dngconverter

import "fmt"

func readExecutableVersion(path string) (string, error) {
	return "", fmt.Errorf("version detection not supported on Linux")
}
