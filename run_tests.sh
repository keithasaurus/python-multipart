#!/usr/bin/env bash

TIME_START=`date +%s`

echo "Running mypy type checks"
mypy --strict multipart || exit $?
mypy --ignore-missing-imports tests examples || exit $?

echo "Running tests with coverage"
coverage run -m unittest discover tests || exit $?

# this number needs to move up big time. only this low temporarily
COVERAGE_THRESHOLD=94
coverage report --fail-under "$COVERAGE_THRESHOLD" > /dev/null 2>&1

COVERAGE_EXIT=$?
if [ "$COVERAGE_EXIT" -ne 0 ]
then
    echo "-------------"
    echo ""
    echo "THE SKY IS FALLING!!! Testing coverage under $COVERAGE_THRESHOLD"
    echo ""
    echo "-------------"
    echo "run 'coverage report' or 'coverage html' to see where the gaps are"
    exit "$COVERAGE_EXIT"
fi

coverage erase


echo "Running Flake8"
flake8 . || exit $?


TIME_END=`date +%s`
RUNTIME=$((TIME_END-TIME_START))

echo "TESTS PASSED IN $RUNTIME SECONDS! YOU ARE A GOOD PERSON! :)"