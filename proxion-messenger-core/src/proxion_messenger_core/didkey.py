"""did:key encoding for Ed25519 public keys.

This module provides functions to encode/decode W3C DID keys for Ed25519.
Format: did:key:z6Mk... where z6Mk is base58btc(0xed01 + 32-byte-pubkey)
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

# First 1024 words of the BIP-39 English wordlist (deterministic safety-number generation)
_SAFETY_WORDS: list[str] = [
    "abandon","ability","able","about","above","absent","absorb","abstract","absurd","abuse",
    "access","accident","account","accuse","achieve","acid","acoustic","acquire","across","act",
    "action","actor","actress","actual","adapt","add","addict","address","adjust","admit",
    "adult","advance","advice","aerobic","afford","afraid","again","age","agent","agree",
    "ahead","aim","air","airport","aisle","alarm","album","alcohol","alert","alien",
    "all","alley","allow","almost","alone","alpha","already","also","alter","always",
    "amateur","amazing","among","amount","amused","analyst","anchor","ancient","anger","angle",
    "angry","animal","ankle","announce","annual","another","answer","antenna","antique","anxiety",
    "any","apart","apology","appear","apple","approve","april","arch","arctic","area",
    "arena","argue","arm","armed","armor","army","around","arrange","arrest","arrive",
    "arrow","art","artefact","artist","artwork","ask","aspect","assault","asset","assist",
    "assume","asthma","athlete","atom","attack","attend","attitude","attract","auction","audit",
    "august","aunt","author","auto","autumn","average","avocado","avoid","awake","aware",
    "away","awesome","awful","awkward","axis","baby","balance","bamboo","banana","banner",
    "bar","barely","bargain","barrel","base","basic","basket","battle","beach","bean",
    "beauty","because","become","beef","before","begin","behave","behind","believe","below",
    "belt","bench","benefit","best","betray","better","between","beyond","bicycle","bid",
    "bike","bind","biology","bird","birth","bitter","black","blade","blame","blanket",
    "blast","bleak","bless","blind","blood","blossom","blouse","blue","blur","blush",
    "board","boat","body","boil","bomb","bone","book","boost","border","boring",
    "borrow","boss","bottom","bounce","box","boy","bracket","brain","brand","brave",
    "breeze","brick","bridge","brief","bright","bring","brisk","broccoli","broken","bronze",
    "broom","brother","brown","brush","bubble","buddy","budget","buffalo","build","bulb",
    "bulk","bullet","bundle","bunker","burden","burger","burst","bus","business","busy",
    "butter","buyer","buzz","cabbage","cabin","cable","cactus","cage","cake","call",
    "calm","camera","camp","can","canal","cancel","candy","cannon","canvas","canyon",
    "capable","capital","captain","carbon","card","cargo","carpet","carry","cart","case",
    "cash","casino","castle","casual","cat","catalog","catch","category","cattle","caught",
    "cause","caution","cave","ceiling","celery","cement","census","century","cereal","certain",
    "chair","chalk","champion","change","chaos","chapter","charge","chase","chat","cheap",
    "check","cheese","chef","cherry","chest","chicken","chief","child","chimney","choice",
    "choose","chronic","chuckle","chunk","churn","cigar","cinnamon","circle","citizen","city",
    "civil","claim","clap","clarify","claw","clay","clean","clerk","clever","click",
    "client","cliff","climb","clinic","clip","clock","clog","close","cloth","cloud",
    "clown","club","clump","cluster","clutch","coach","coast","coconut","code","coffee",
    "coil","coin","collect","color","column","combine","come","comfort","comic","common",
    "company","concert","conduct","confirm","congress","connect","consider","control","convince","cook",
    "cool","copper","copy","coral","core","corn","correct","cost","cotton","couch",
    "country","couple","course","cousin","cover","coyote","crack","cradle","craft","cram",
    "crane","crash","crater","crawl","crazy","cream","credit","creek","crew","cricket",
    "crime","crisp","critic","cross","crouch","crowd","crucial","cruel","cruise","crumble",
    "crunch","crush","cry","crystal","cube","culture","cup","cupboard","curious","current",
    "curtain","curve","cushion","custom","cute","cycle","dad","damage","damp","dance",
    "danger","daring","dash","daughter","dawn","day","deal","debate","debris","decade",
    "december","decide","decline","decorate","decrease","deer","defense","define","defy","degree",
    "delay","deliver","demand","demise","denial","dentist","deny","depart","depend","deposit",
    "depth","deputy","derive","describe","desert","design","desk","despair","destroy","detail",
    "detect","develop","device","devote","diagram","dial","diamond","diary","dice","diesel",
    "diet","differ","digital","dignity","dilemma","dinner","dinosaur","direct","dirt","disagree",
    "discover","disease","dish","dismiss","disorder","display","distance","divert","divide","divorce",
    "dizzy","doctor","document","dog","doll","dolphin","domain","donate","donkey","donor",
    "door","dose","double","dove","draft","dragon","drama","drastic","draw","dream",
    "dress","drift","drill","drink","drip","drive","drop","drum","dry","duck",
    "dumb","dune","during","dust","dutch","duty","dwarf","dynamic","eager","eagle",
    "early","earn","earth","easily","east","easy","echo","ecology","edge","edit",
    "educate","effort","egg","eight","either","elbow","elder","electric","elegant","element",
    "elephant","elevator","elite","else","embark","embody","embrace","emerge","emotion","employ",
    "empower","empty","enable","enact","endless","endorse","enemy","energy","enforce","engage",
    "engine","enhance","enjoy","enlist","enough","enrich","enroll","ensure","enter","entire",
    "entry","envelope","episode","equal","equip","erase","erode","erosion","error","erupt",
    "escape","essay","essence","estate","eternal","ethics","evidence","evil","evoke","evolve",
    "exact","example","excess","exchange","excite","exclude","exercise","exhaust","exhibit","exile",
    "exist","exit","exotic","expand","expire","explain","expose","express","extend","extra",
    "eye","fable","face","faculty","faint","faith","fall","false","fame","family",
    "famous","fan","fancy","fantasy","far","fashion","fat","fatal","father","fatigue",
    "fault","favorite","feature","february","federal","fee","feed","feel","feet","fellow",
    "felt","fence","festival","fetch","fever","few","fiber","fiction","field","figure",
    "file","film","filter","final","find","fine","finger","finish","fire","firm",
    "first","fiscal","fish","fit","fitness","fix","flag","flame","flash","flat",
    "flavor","flee","flight","flip","float","flock","floor","flower","fluid","flush",
    "fly","foam","focus","fog","foil","follow","food","foot","force","forest",
    "forget","fork","fortune","forum","forward","fossil","foster","found","fox","fragile",
    "frame","frequent","fresh","friend","front","frost","frown","frozen","fruit","fuel",
    "fun","funny","furnace","fury","future","gadget","gain","galaxy","gallery","game",
    "gap","garbage","garden","garlic","garment","gas","gasp","gate","gather","gauge",
    "gaze","general","genius","genre","gentle","genuine","gesture","ghost","giant","gift",
    "giggle","ginger","giraffe","girl","give","glad","glance","glare","glass","glide",
    "glimpse","globe","gloom","glory","glove","glow","glue","goat","goddess","gold",
    "good","goose","gorilla","gospel","gossip","govern","gown","grab","grace","grain",
    "grant","grape","grasp","grass","gravity","great","green","grid","grief","grit",
    "grocery","group","grow","grunt","guard","guide","guilt","guitar","gun","gym",
]


def fingerprint_words(pub_bytes: bytes) -> list[str]:
    """Return 6 safety words derived from SHA-256 of pub_bytes.

    Splits the 32-byte digest into 6 segments; each is reduced mod 1024 to
    index _SAFETY_WORDS. Same input always produces same words.
    """
    digest = hashlib.sha256(pub_bytes).digest()  # 32 bytes
    words = []
    # Use bytes 0-4, 5-9, 10-14, 15-19, 20-24, 25-29 (5 bytes each → 40 bits, mod 1024)
    _word_count = len(_SAFETY_WORDS)
    for i in range(6):
        chunk = digest[i * 5: i * 5 + 5]
        idx = int.from_bytes(chunk, "big") % _word_count
        words.append(_SAFETY_WORDS[idx])
    return words

if TYPE_CHECKING:
    from .persist import AgentState


# base58btc alphabet per W3C spec
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_MAP = {c: i for i, c in enumerate(BASE58_ALPHABET)}
MULTICODEC_ED25519_PUB = b"\xed\x01"


def _base58btc_encode(data: bytes) -> str:
    """Encode bytes to base58btc string (W3C base58btc alphabet).
    Leading zero bytes become leading '1' characters.
    """
    if not data:
        return ""
    num = int.from_bytes(data, byteorder="big")
    encoded = ""
    while num > 0:
        num, remainder = divmod(num, 58)
        encoded = BASE58_ALPHABET[remainder] + encoded
    leading_ones = len(data) - len(data.lstrip(b"\x00"))
    return "1" * leading_ones + encoded


def _base58btc_decode(encoded: str) -> bytes:
    """Decode base58btc string back to bytes.
    Raises ValueError if the input contains invalid base58 characters.
    """
    leading_ones = len(encoded) - len(encoded.lstrip("1"))
    num = 0
    for char in encoded[leading_ones:]:
        idx = _BASE58_MAP.get(char)
        if idx is None:
            raise ValueError(f"Invalid base58btc character: {char}")
        num = num * 58 + idx
    result = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    return b"\x00" * leading_ones + result


def _validate_ed25519_multicodec(multicodec_bytes: bytes) -> None:
    """Raise ValueError if bytes are not a valid Ed25519 multicodec payload (0xed01 + 32 bytes)."""
    if multicodec_bytes[:2] != MULTICODEC_ED25519_PUB or len(multicodec_bytes) != 34:
        raise ValueError(
            f"Not an Ed25519 key: expected {MULTICODEC_ED25519_PUB.hex()} + 32 bytes, "
            f"got {multicodec_bytes.hex()}"
        )


def pub_key_to_did(pub_key_bytes: bytes) -> str:
    """Encode a 32-byte Ed25519 public key as a did:key DID.
    
    Parameters
    ----------
    pub_key_bytes : bytes
        Raw Ed25519 public key (32 bytes).
    
    Returns
    -------
    str
        DID in the format "did:key:z6Mk..." where the suffix is the
        base58btc-encoded multicodec (0xed01 + public key bytes).
    """
    if len(pub_key_bytes) != 32:
        raise ValueError(f"Expected 32-byte Ed25519 key, got {len(pub_key_bytes)} bytes")
    
    # Prepend Ed25519 multicodec prefix
    multicodec_bytes = MULTICODEC_ED25519_PUB + pub_key_bytes
    
    # Encode to base58btc
    encoded = _base58btc_encode(multicodec_bytes)
    
    # Return as did:key DID
    return f"did:key:z{encoded}"


def did_to_pub_key(did: str) -> bytes:
    """Decode a did:key DID back to raw Ed25519 public key bytes.
    
    Parameters
    ----------
    did : str
        DID string in the format "did:key:z6Mk...".
    
    Returns
    -------
    bytes
        The 32-byte Ed25519 public key.
    
    Raises
    ------
    ValueError
        If the DID is not a valid did:key Ed25519 DID.
    """
    if not did.startswith("did:key:z"):
        raise ValueError(f"Invalid did:key format: {did}")
    try:
        multicodec_bytes = _base58btc_decode(did[9:])
    except ValueError as e:
        raise ValueError(f"Invalid base58btc in DID: {e}") from e
    _validate_ed25519_multicodec(multicodec_bytes)
    return multicodec_bytes[2:]


def agent_did(agent_state: AgentState) -> str:
    """Return the did:key DID for an AgentState.
    
    Derives the public key from agent_state.identity_key and encodes it.
    
    Parameters
    ----------
    agent_state : AgentState
        The agent state containing the identity key.
    
    Returns
    -------
    str
        The did:key DID for this agent.
    """
    # Extract public key bytes from the Ed25519 private key
    from cryptography.hazmat.primitives import serialization
    
    pub_key = agent_state.identity_key.public_key()
    pub_bytes = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    
    return pub_key_to_did(pub_bytes)
