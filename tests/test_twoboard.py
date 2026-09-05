"""twoboard builds the two-board recipe's command lines without starting anything."""

from pathlib import Path

from tools.cdj_main import twoboard


def _plan(tmp_path, *extra):
    argv = ["run-1", "--card", str(tmp_path / "card.img"),
            "--runs-dir", str(tmp_path / "runs"), "--work-dir", str(tmp_path / "work"), *extra]
    return twoboard.build_plan(twoboard.parse_args(argv), environ={})


def test_default_recipe_is_the_one_that_held(tmp_path):
    plan = _plan(tmp_path)
    assert plan.run_dir == tmp_path / "runs" / "run-1"
    assert plan.work_card == tmp_path / "work" / "card-work.img"
    bv = plan.boot_vm
    assert bv[bv.index("--sd") + 1] == str(plan.work_card)
    assert bv[bv.index("--source-key-at") + 1] == "60"
    assert "--poll-words" not in bv                      # the gdb stub stays free
    injects = [plan.proxy[i + 1] for i, a in enumerate(plan.proxy) if a == "--inject"]
    assert injects == list(twoboard.DEFAULT_INJECTS)
    assert "BFIN_MAIN_LINK=127.0.0.1:5990" in plan.gui
    assert plan.env["CDJ_QEMU"] == twoboard.DEFAULT_QEMU
    assert plan.keys == []


def test_options_reach_the_right_process(tmp_path):
    keys = tmp_path / "keys.txt"
    keys.write_text("# PLAY late\n230 press 16.0\n100 rotary 4 +1\n", encoding="utf-8")
    plan = _plan(tmp_path, "--keys", str(keys), "--inject", "50:1:3:7:1:0", "--env", "CDJ_DSP_ACK=1",
                 "--gui-env", "BFIN_LINK_RX_CENSUS=10", "--bootvm-arg=--trace=0x41bd304",
                 "--poll-words", "0x489bc88")
    assert plan.keys == [(100, ["rotary", "4", "+1"]), (230, ["press", "16.0"])]
    assert plan.proxy.count("--inject") == 1 and "50:1:3:7:1:0" in plan.proxy
    assert plan.env["CDJ_DSP_ACK"] == "1"
    assert "BFIN_LINK_RX_CENSUS=10" in plan.gui
    assert "--trace=0x41bd304" in plan.boot_vm
    assert plan.boot_vm[plan.boot_vm.index("--poll-words") + 1] == "0x489bc88"


def test_no_inject_and_describe(tmp_path):
    plan = _plan(tmp_path, "--no-inject")
    assert "--inject" not in plan.proxy
    text = twoboard.describe(plan)
    assert "boot_vm:" in text and "keys: none" in text


def test_existing_run_dir_is_refused(tmp_path):
    plan = _plan(tmp_path)
    plan.run_dir.mkdir(parents=True)
    assert twoboard.run(plan, Path(tmp_path / "card.img")) == 2
