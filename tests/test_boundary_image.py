"""U4's one check inside the built serving image (FR6.2.2, SD-U4-5, P-INF-U4-1).

Marked `image` and deselected from the default run (pyproject addopts). Run it with:

    .venv/bin/python -m pytest -m image tests/test_boundary_image.py -v

The image is built by U3's `image_tag` fixture, imported here and not copied: the context is
staged by the shared in-process synth of the REAL router stack, then `docker build --platform
linux/arm64` runs on that CDK-staged directory. The container runs with `--network none`.

  T-IMG-U4-1  inside the built image, `delphi.router.learned.LearnedRouter` has no learned-router
              fallback method, and `RouterAgent.__init__` accepts `fallback_alias`: the image
              carries U4's agent-level fallback. At U4's own commit this runs on the U4-tree
              image (P-INF-U4-1); on the image U6 will push it is the binding pre-deploy check,
              recorded `not run` until U6 runs it (NR-1). It says nothing about what any
              already-deployed arm runs.

Partition `image`: needs a running Docker daemon able to build linux/arm64. No AWS call.
When selected, the test FAILS, not skips, without Docker.
"""
from __future__ import annotations

import pytest
from test_packaging_image import _run_in, image_tag  # noqa: F401 - image_tag is a fixture

pytestmark = pytest.mark.image

#: The removed method's name, assembled so the literal occurs in no file but T-BND-U4-4's.
_REMOVED = "predict" + "_with_fallback"

PROBE = (
    "import inspect\n"
    "from delphi.agent.app import RouterAgent\n"
    "from delphi.router.learned import LearnedRouter\n"
    f"print(hasattr(LearnedRouter, {_REMOVED!r}),\n"
    "      'fallback_alias' in inspect.signature(RouterAgent.__init__).parameters)\n"
)


def test_t_img_u4_1_the_image_carries_the_agent_level_fallback(image_tag):  # noqa: F811 - image_tag is the imported fixture
    res = _run_in(image_tag, "python", "-c", PROBE)
    assert res.returncode == 0, res.stderr
    assert res.stdout.split() == ["False", "True"], res.stdout
