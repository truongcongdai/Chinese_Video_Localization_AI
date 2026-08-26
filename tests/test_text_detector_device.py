import sys
from pathlib import Path
from types import SimpleNamespace

from universal_video_ai.render.text_detector import OnScreenTextDetector


def _fake_torch(cuda_available: bool, gpu_name: str = "Test GPU"):
    return SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: cuda_available,
            current_device=lambda: 0,
            get_device_name=lambda _index: gpu_name,
            empty_cache=lambda: None,
        ),
        backends=SimpleNamespace(
            mps=SimpleNamespace(is_available=lambda: False),
        ),
    )


def test_easyocr_auto_selects_cuda(monkeypatch):
    calls = []

    class FakeReader:
        def __init__(self, languages, gpu):
            calls.append((languages, gpu))
            self.device = gpu

    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True))
    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=FakeReader))

    reader = OnScreenTextDetector(device="auto")._get_reader()

    assert reader.device == "cuda"
    assert calls == [(["ch_sim", "en"], "cuda")]


def test_easyocr_auto_falls_back_to_cpu_without_gpu(monkeypatch):
    calls = []

    class FakeReader:
        def __init__(self, languages, gpu):
            calls.append((languages, gpu))
            self.device = "cpu" if gpu is False else gpu

    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=False))
    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=FakeReader))

    reader = OnScreenTextDetector(device="auto")._get_reader()

    assert reader.device == "cpu"
    assert calls == [(["ch_sim", "en"], False)]


def test_easyocr_auto_retries_cpu_when_cuda_initialization_fails(monkeypatch):
    calls = []

    class FakeReader:
        def __init__(self, _languages, gpu):
            calls.append(gpu)
            if gpu == "cuda":
                raise RuntimeError("CUDA out of memory")
            self.device = "cpu"

    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True))
    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=FakeReader))

    reader = OnScreenTextDetector(device="auto")._get_reader()

    assert reader.device == "cpu"
    assert calls == ["cuda", False]


def test_frame_grid_uses_one_sequential_ffmpeg_decode(tmp_path, monkeypatch):
    detector = OnScreenTextDetector()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        pattern = Path(cmd[-1])
        pattern.parent.mkdir(parents=True, exist_ok=True)
        (pattern.parent / "frame_00000000.jpg").write_bytes(b"frame")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("subprocess.run", fake_run)

    grid = detector._extract_frame_grid(video, tmp_path, duration=10.0, fps=10.0)

    assert grid is not None
    assert len(captured) == 1
    assert "fps=10.000000:start_time=0" in captured[0]
    assert detector._frame_from_grid(grid, 0.0).name == "frame_00000000.jpg"
