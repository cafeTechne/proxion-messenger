// Pod votes are atomic actor-owned choices, bound to the exact poll revision.
// Input operations here have already passed room-change signature verification.
import { parsePoll, POLL_EMOJI } from './polls.js';

export async function roomPollRevision(message) {
    if (!message || message.deleted || typeof message.content !== 'string' || !parsePoll(message.content)) return null;
    const bytes = new TextEncoder().encode(JSON.stringify([message.revision_id || '', message.content]));
    return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
}
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
export async function projectRoomVotes(messages, verifiedChanges) {
    for (const message of messages) {
        const revision = await roomPollRevision(message);
        if (!revision) continue;
        message.poll_revision = revision;
        message.poll_vote_versions = {};
        for (const emoji of POLL_EMOJI) delete message.reactions[emoji];
        const changes = [...(verifiedChanges.get(message.iri) || [])].filter(c => c.kind === 'vote')
            .sort((a, b) => compare(a.created, b.created) || compare(a.id, b.id) || compare(a.proof, b.proof));
        let olderVotes = false;
        for (const c of changes) {
            if (c.poll_revision === revision) message.poll_vote_versions[c.actor] = c;
            else olderVotes = true;
        }
        const choices = new Set(parsePoll(message.content).options.map(option => option.emoji));
        message.poll_votes_reset = !!message.revision_id && olderVotes && !Object.keys(message.poll_vote_versions).length;
        for (const vote of Object.values(message.poll_vote_versions)) {
            if (!choices.has(vote.content)) continue; // empty content clears the vote
            if (!message.reactions[vote.content]) message.reactions[vote.content] = [];
            message.reactions[vote.content].push(vote.actor);
        }
        for (const emoji of POLL_EMOJI) message.reactions[emoji]?.sort(compare);
    }
    return messages;
}
