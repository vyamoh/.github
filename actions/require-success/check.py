import json
import os
import sys


def failures(results):
    if not isinstance(results, dict) or not results:
        raise ValueError("Expected a non-empty object of required job results")
    return [name for name, job in results.items()
            if not isinstance(job, dict) or job.get("result") != "success"]


def main():
    try:
        failed = failures(json.loads(os.environ["JOB_RESULTS"]))
    except (KeyError, ValueError, TypeError) as error:
        print(f"Invalid required job results: {error}", file=sys.stderr)
        return 1
    if failed:
        print("Required jobs did not succeed: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
