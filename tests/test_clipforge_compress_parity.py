"""Keep ClipForge encoder arguments; require the decoded-frame boundary."""
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

    def rebuilt(source, candidate, command):
        encoded(command)

    upstream = upstream_module('compress')
    with patch.object(common, 'sh', side_effect=encoded), \
         patch.object(upstream, '_validate'), patch.object(compress, '_validate'), \
         patch('pipeline.frame_rebuild.encode_decoded_frames', side_effect=rebuilt):
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

    def rebuilt(source, candidate, command):
        Path(candidate).write_bytes(candidate_bytes)

    with patch('pipeline.frame_rebuild.encode_decoded_frames', side_effect=rebuilt), \
         patch.object(compress, '_validate'):
        compress.compress(source, destination)
    assert destination.read_bytes() == candidate_bytes


def test_size_fallback_never_restores_even_an_already_valid_original(tmp_path):
    source = tmp_path / 'merged.mp4'
    destination = tmp_path / 'final.mp4'
    original = b'already-valid-original'
    source.write_bytes(original)

    def rebuilt(source, candidate, command):
        Path(candidate).write_bytes(b'larger-constrained-delivery-candidate')

    with patch('pipeline.frame_rebuild.encode_decoded_frames', side_effect=rebuilt), \
         patch.object(compress, '_validate'):
        compress.compress(source, destination)
    assert destination.read_bytes() == b'larger-constrained-delivery-candidate'
