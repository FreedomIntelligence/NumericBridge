"""Build and smoke-test a NumericBridge Workflow release ZIP.

From a clean checkout with development requirements and CPU Torch installed:
  python -B -m scripts.workflow_release --version v1.2.3 --output /tmp/workflow-dist
"""

from .skills_release import main


if __name__ == '__main__':
    main(distribution='workflow', description=__doc__)
