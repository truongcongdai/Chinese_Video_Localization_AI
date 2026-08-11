import sys
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
