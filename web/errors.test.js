import { describe, it, expect, vi } from 'vitest';
import { knownErrorKey, friendlyErrorKey, GENERIC_ERROR_KEY } from './errors.js';

describe('gateway error mapping', () => {
  it('maps exact codes to their keys', () => {
    expect(knownErrorKey('room_full')).toBe('error.room_full');
    expect(knownErrorKey('Not registered')).toBe('error.not_registered');
  });
  it('maps the room moderation errors a member or owner can hit', () => {
    expect(knownErrorKey('you_are_muted')).toBe('error.you_are_muted');
    expect(knownErrorKey('insufficient_permissions')).toBe('error.insufficient_permissions');
    expect(knownErrorKey('That user is not in this room or is offline')).toBe('error.transfer_target_offline');
  });
  it('maps the invite-not-found sentence by prefix, ignoring the code', () => {
    expect(knownErrorKey('No room found with invite code abc123')).toBe('error.inviteNotFound');
  });
  it('maps file_type_not_allowed with a suffix', () => {
    expect(knownErrorKey('file_type_not_allowed: .exe')).toBe('error.file_type_not_allowed');
  });
  it('falls back to the generic key and logs the raw text', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(friendlyErrorKey('Some internal traceback')).toBe(GENERIC_ERROR_KEY);
    expect(warn).toHaveBeenCalledWith(expect.any(String), 'Some internal traceback');
    warn.mockRestore();
  });
  it('does not log known errors', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(friendlyErrorKey('room_not_found')).toBe('error.room_not_found');
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });
  it('treats an empty message as unknown', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(friendlyErrorKey('')).toBe(GENERIC_ERROR_KEY);
    warn.mockRestore();
  });
});

describe('gate errors', () => {
  it('maps the scheduled-DM refusal and the revoked / safe-mode gates', () => {
    expect(knownErrorKey('schedule_dm_encrypted')).toBe('error.schedule_dm_encrypted');
    expect(knownErrorKey('identity_revoked')).toBe('error.identity_revoked');
    expect(knownErrorKey('safe_mode_enabled')).toBe('error.safe_mode_enabled');
  });
});
