"""
Danh sách từ 5 chữ cái dùng cho mini game Wordle (/game -> Wordle).

Có 2 bộ từ:
- WORDLE_WORDS_EN: tiếng Anh, 5 chữ cái, các từ thông dụng.
- WORDLE_WORDS_VI: tiếng Việt KHÔNG DẤU, 5 chữ cái (mode "Việt hoá").

Muốn thêm từ mới: chỉ cần thêm chuỗi (chữ thường, đúng 5 ký tự, không dấu/không
khoảng trắng) vào list tương ứng bên dưới.
"""

WORDLE_WORDS_EN = [
    "about", "above", "abuse", "actor", "acute", "admit", "adopt", "adult",
    "after", "again", "agent", "agree", "ahead", "alarm", "album", "alert",
    "alike", "alive", "allow", "alone", "along", "alter", "among", "anger",
    "angle", "angry", "apart", "apple", "apply", "arena", "argue", "arise",
    "array", "aside", "asset", "audio", "audit", "avoid", "awake", "award",
    "aware", "badly", "baker", "bases", "basic", "basis", "beach", "began",
    "begin", "being", "below", "bench", "billy", "birth", "black", "blame",
    "blank", "blast", "blind", "block", "blood", "board", "boost", "booth",
    "bound", "brain", "brand", "bread", "break", "breed", "brief", "bring",
    "broad", "broke", "brown", "build", "built", "buyer", "cable", "calif",
    "carry", "catch", "cause", "chain", "chair", "chart", "chase", "cheap",
    "check", "chest", "chief", "child", "china", "chose", "civil", "claim",
    "class", "clean", "clear", "click", "clock", "close", "coach", "coast",
    "could", "count", "court", "cover", "craft", "crash", "cream", "crime",
    "cross", "crowd", "crown", "curve", "cycle", "daily", "dance", "dated",
    "dealt", "death", "debut", "delay", "depth", "doing", "doubt", "dozen",
    "draft", "drama", "drawn", "dream", "dress", "drill", "drink", "drive",
    "drove", "dying", "eager", "early", "earth", "eight", "elite", "empty",
    "enemy", "enjoy", "enter", "entry", "equal", "error", "event", "every",
    "exact", "exist", "extra", "faith", "false", "fault", "fiber", "field",
    "fifth", "fifty", "fight", "final", "first", "fixed", "flash", "fleet",
    "floor", "fluid", "focus", "force", "forth", "forty", "forum", "found",
    "frame", "frank", "fraud", "fresh", "front", "fruit", "fully", "funny",
    "giant", "given", "glass", "globe", "going", "grace", "grade", "grand",
    "grant", "grass", "great", "green", "gross", "group", "grown", "guard",
    "guess", "guest", "guide", "happy", "harsh", "heart", "heavy", "hence",
    "horse", "hotel", "house", "human", "ideal", "image", "index", "inner",
    "input", "issue", "japan", "joint", "jones", "judge", "known", "label",
    "large", "laser", "later", "laugh", "layer", "learn", "lease", "least",
    "leave", "legal", "level", "light", "limit", "links", "lives", "local",
    "logic", "loose", "lower", "lucky", "lunch", "lying", "magic", "major",
    "maker", "march", "match", "maybe", "mayor", "meant", "media", "metal",
    "might", "minor", "minus", "mixed", "model", "money", "month", "moral",
    "motor", "mount", "mouse", "mouth", "moved", "movie", "music", "needs",
    "never", "newer", "night", "noise", "north", "noted", "novel", "nurse",
    "occur", "ocean", "offer", "often", "order", "other", "ought", "paint",
    "panel", "paper", "party", "peace", "phase", "phone", "photo", "piece",
    "pilot", "pitch", "place", "plain", "plane", "plant", "plate", "point",
    "pound", "power", "press", "price", "pride", "prime", "print", "prior",
    "prize", "proof", "proud", "prove", "queen", "quick", "quiet", "quite",
    "radio", "raise", "range", "rapid", "ratio", "reach", "ready", "realm",
    "rebel", "refer", "relax", "reply", "right", "rival", "river", "robot",
    "roman", "rough", "round", "route", "royal", "rural", "scale", "scene",
    "scope", "score", "sense", "serve", "seven", "shall", "shape", "share",
    "sharp", "sheet", "shelf", "shell", "shift", "shine", "shirt", "shock",
    "shoot", "short", "shown", "sight", "since", "sixth", "sized", "skill",
    "sleep", "slide", "small", "smart", "smile", "smith", "smoke", "solid",
    "solve", "sorry", "sound", "south", "space", "spare", "speak", "speed",
    "spend", "spent", "split", "spoke", "sport", "staff", "stage", "stake",
    "stand", "start", "state", "steam", "steel", "stick", "still", "stock",
    "stone", "stood", "store", "storm", "story", "strip", "stuck", "study",
    "stuff", "style", "sugar", "suite", "super", "sweet", "table", "taken",
    "taste", "taxes", "teach", "teeth", "teens", "terms", "thank", "theft",
    "their", "theme", "there", "these", "thick", "thing", "think", "third",
    "those", "three", "throw", "tight", "times", "tired", "title", "today",
    "topic", "total", "touch", "tough", "tower", "track", "trade", "train",
    "treat", "trend", "trial", "tribe", "trick", "tried", "tries", "truck",
    "truly", "trust", "truth", "twice", "under", "undue", "union", "unity",
    "until", "upper", "upset", "urban", "usage", "usual", "valid", "value",
    "video", "virus", "visit", "vital", "voice", "waste", "watch", "water",
    "wheel", "where", "which", "while", "white", "whole", "whose", "woman",
    "women", "world", "worry", "worse", "worst", "worth", "would", "wound",
    "write", "wrong", "wrote", "yield", "young", "youth",
]

# Mỗi phần tử là 1 từ ghép tiếng Việt (2 âm tiết, bỏ dấu, viết liền) đúng
# 5 chữ cái, ví dụ "banbe" = "bạn bè", "conca" = "con cá". Có thể thêm bớt
# thoải mái, miễn giữ đúng 5 ký tự chữ thường không dấu/không khoảng trắng.
WORDLE_WORDS_VI = [
    "banbe", "quaba", "ngaho", "songn", "trole",
    "hocba", "dieuk", "vango", "nangm", "themt",
]
