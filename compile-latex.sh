#!/bin/bash

# Compile a latex assignment twice.

# TEST
readonly THIS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
readonly BASE_DIR="${THIS_DIR}/.."

function compile() {
    local path=$1
    local pass=$2

    local name=$(basename "${path}" '.tex')
    local dir=$(dirname "${path}")

    local log_path="${dir}/${name}_compile_${pass}.txt"

    pdflatex "${path}" &> "${log_path}"
    local error_code=$?

    if [[ ${error_code} -ne 0 ]]; then
        echo "Compile pass ${pass} failed with code ${error_code}. See log: '${log_path}'."
        exit 20
    fi

    return 0
}

function check_requirements() {
    type pdflatex > /dev/null 2> /dev/null
    if [[ "$?" -ne 0 ]]; then
        echo "Could not find pdflatex."
        exit 10
    fi
}

function main() {
    if [[ $# -ne 1 ]]; then
        echo "USAGE: $0 <path to latex file>"
        exit 1
    fi

    trap exit SIGINT

    check_requirements

    compile "$1" 1
    compile "$1" 2

    return 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
