# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: TR-959 — neutralising Kokoro's dead LSTM dropout so MIOpen can load the model.

Unit-level and torch-only: the property under test is about module attributes, not about
audio, so it needs neither Kokoro nor a GPU. The integration side (that synthesis actually
runs) is tests/integration/test_tts.py.
"""

import pytest

from mamemai_server.infrastructure.tts import _neutralise_lstm_dropout

torch = pytest.importorskip("torch")
nn = torch.nn


def _kokoro_shaped_model() -> nn.Module:
    """Kokoro's DurationEncoder shape: single-layer bidirectional LSTMs carrying a
    dropout argument that can never fire, interleaved with other modules."""
    return nn.Sequential(
        nn.LSTM(640, 256, num_layers=1, batch_first=True, bidirectional=True, dropout=0.1),
        nn.Linear(512, 512),
        nn.Sequential(
            nn.LSTM(640, 256, num_layers=1, batch_first=True, bidirectional=True, dropout=0.1),
        ),
    )


def test_zeroes_dropout_on_every_nested_lstm():
    """Nested, because Kokoro's LSTMs sit several levels down inside KModel — walking only
    the top level would silently patch nothing."""
    model = _kokoro_shaped_model()

    patched = _neutralise_lstm_dropout(model)

    assert patched == 2
    assert all(m.dropout == 0.0 for m in model.modules() if isinstance(m, nn.LSTM))


def test_reports_zero_when_there_is_nothing_to_patch():
    """The return value is the guard against a silent no-op: if Kokoro ever ships LSTMs
    built without the dropout argument, or restructures so they are not reachable, this
    is what says so rather than the next ROCm run failing mysteriously."""
    model = nn.Sequential(nn.LSTM(64, 32, num_layers=1, batch_first=True))

    assert _neutralise_lstm_dropout(model) == 0


def test_leaves_everything_but_lstm_dropout_alone():
    """A blunt "set every dropout to zero" would change real behaviour elsewhere; this
    must touch the LSTM argument and nothing else."""
    model = nn.Sequential(
        nn.LSTM(64, 32, num_layers=1, batch_first=True, dropout=0.1),
        nn.Dropout(p=0.5),
    )

    _neutralise_lstm_dropout(model)

    assert next(m for m in model.modules() if isinstance(m, nn.Dropout)).p == 0.5


def test_zeroing_does_not_change_the_output():
    """The claim that makes this safe to apply unconditionally rather than only on ROCm.
    Single-layer LSTM dropout is dead configuration — PyTorch itself warns that it cannot
    apply — so the same weights and input must produce bit-identical output either way."""
    torch.manual_seed(11)
    model = nn.LSTM(64, 32, num_layers=1, batch_first=True, bidirectional=True, dropout=0.1).eval()
    x = torch.randn(1, 40, 64)

    with torch.no_grad():
        before, _ = model(x)
    _neutralise_lstm_dropout(nn.Sequential(model))
    with torch.no_grad():
        after, _ = model(x)

    assert torch.equal(before, after)
