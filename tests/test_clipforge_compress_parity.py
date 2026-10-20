"""Keep the terminal pass literally upstream except the owner's rate ceiling."""
from pathlib import Path
from unittest.mock import patch

from pipeline.clipforge_vendor import common, compress
from tests.test_clipforge_export import upstream_module


def test_terminal_command_matches_upstream_argument_order(tmp_path):
    source = tmp_path / 'merged.mp4'
    destination = tmp_path / 'final.mp4'
    source.write_bytes(b'x' * 100)
    commands = []

    def encoded(command):
        commands.append(command.copy())
        Path(command[-1]).write_bytes(b'y' * 50)

    upstream = upstream_module('compress')
    with patch.object(common, 'sh', side_effect=encoded), \
         patch.object(upstream, '_validate'), patch.object(compress, '_validate'):
        # Functions executed from the upstream namespace keep that globals dict.
        with patch.dict(upstream.compress.__globals__, {'_validate': lambda *args: None}):
            upstream.compress(source, destination)
        compress.compress(source, destination)

    reference, ported = commands
    index = ported.index('-maxrate')
    assert ported[index:index + 4] == ['-maxrate', '1800k', '-bufsize', '1800k']
    del ported[index:index + 4]
    assert reference == ported


def test_size_fallback_cannot_restore_an_over_limit_original(tmp_path):
    source = tmp_path / 'merged.mp4'
    destination = tmp_path / 'final.mp4'
    source.write_bytes(b'over-limit-original')
    candidate_bytes = b'constrained-candidate-larger-than-original'

    def encoded(command):
        Path(command[-1]).write_bytes(candidate_bytes)

    with patch.object(common, 'sh', side_effect=encoded), \
         patch.object(compress, '_validate'), \
         patch('pipeline.export_checks.rate_within_limit', return_value=False):
        compress.compress(source, destination)
    assert destination.read_bytes() == candidate_bytes


def test_size_fallback_retains_only_an_already_valid_original(tmp_path):
    source = tmp_path / 'merged.mp4'
    destination = tmp_path / 'final.mp4'
    original = b'already-valid-original'
    source.write_bytes(original)

    def encoded(command):
        Path(command[-1]).write_bytes(b'larger-constrained-delivery-candidate')

    with patch.object(common, 'sh', side_effect=encoded), \
         patch.object(compress, '_validate'), \
         patch('pipeline.export_checks.rate_within_limit', return_value=True):
        compress.compress(source, destination)
    assert destination.read_bytes() == original
