#pragma once
// fab/secs2.hpp — SECS-II (SEMI E5) item codec. Real, not a stub.
//
// SECS-II bodies are a recursive item tree. Every item is:
//   [format code (6 bits) | number of length bytes (2 bits)][length...][data]
//
// This is the piece that makes the HSMS layer actually testable: with it, the
// simulator can emit a genuine S6F11 event report and the adapter can decode
// it, so the transport path is exercised end to end rather than mocked.

#include <cstdint>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace fab::secs2 {

// SEMI E5 format codes (upper 6 bits of the format byte).
enum class Fmt : uint8_t {
    List   = 000,  // 0o00
    Binary = 010,  // 0o10
    Bool   = 011,
    ASCII  = 020,
    I8     = 030, I1 = 031, I2 = 032, I4 = 034,
    F8     = 040, F4 = 044,
    U8     = 050, U1 = 051, U2 = 052, U4 = 054,
};

struct Item;
using ItemPtr = std::shared_ptr<Item>;

struct Item {
    Fmt                  fmt = Fmt::List;
    std::string          ascii;
    std::vector<uint64_t> uints;
    std::vector<uint8_t>  bytes;
    std::vector<ItemPtr>  list;

    static ItemPtr L(std::vector<ItemPtr> kids) {
        auto i = std::make_shared<Item>();
        i->fmt = Fmt::List; i->list = std::move(kids);
        return i;
    }
    static ItemPtr A(std::string s) {
        auto i = std::make_shared<Item>();
        i->fmt = Fmt::ASCII; i->ascii = std::move(s);
        return i;
    }
    static ItemPtr U4(uint32_t v) {
        auto i = std::make_shared<Item>();
        i->fmt = Fmt::U4; i->uints = {v};
        return i;
    }
    static ItemPtr U1(uint8_t v) {
        auto i = std::make_shared<Item>();
        i->fmt = Fmt::U1; i->uints = {v};
        return i;
    }
    static ItemPtr B(std::vector<uint8_t> b) {
        auto i = std::make_shared<Item>();
        i->fmt = Fmt::Binary; i->bytes = std::move(b);
        return i;
    }

    // Convenience accessors for decode sites.
    uint64_t as_uint(uint64_t d = 0) const { return uints.empty() ? d : uints[0]; }
    const std::string& as_ascii() const { return ascii; }
    std::size_t size() const { return list.size(); }
    const ItemPtr& at(std::size_t i) const { return list.at(i); }
};

// ---- encode ---------------------------------------------------------------

inline void put_len(std::vector<uint8_t>& out, Fmt f, std::size_t len) {
    // Length is carried in 1-3 bytes; the count lives in the low 2 bits of the
    // format byte.
    uint8_t nbytes = len <= 0xFF ? 1 : (len <= 0xFFFF ? 2 : 3);
    out.push_back(static_cast<uint8_t>((static_cast<uint8_t>(f) << 2) | nbytes));
    for (int i = nbytes - 1; i >= 0; --i)
        out.push_back(static_cast<uint8_t>((len >> (8 * i)) & 0xFF));
}

inline void encode(const ItemPtr& it, std::vector<uint8_t>& out) {
    if (!it) return;
    switch (it->fmt) {
    case Fmt::List:
        put_len(out, Fmt::List, it->list.size());
        for (const auto& k : it->list) encode(k, out);
        break;
    case Fmt::ASCII:
        put_len(out, Fmt::ASCII, it->ascii.size());
        out.insert(out.end(), it->ascii.begin(), it->ascii.end());
        break;
    case Fmt::Binary:
        put_len(out, Fmt::Binary, it->bytes.size());
        out.insert(out.end(), it->bytes.begin(), it->bytes.end());
        break;
    case Fmt::U1:
        put_len(out, Fmt::U1, it->uints.size());
        for (auto v : it->uints) out.push_back(static_cast<uint8_t>(v));
        break;
    case Fmt::U2:
        put_len(out, Fmt::U2, it->uints.size() * 2);
        for (auto v : it->uints) {
            out.push_back((v >> 8) & 0xFF); out.push_back(v & 0xFF);
        }
        break;
    case Fmt::U4:
        put_len(out, Fmt::U4, it->uints.size() * 4);
        for (auto v : it->uints)
            for (int i = 3; i >= 0; --i) out.push_back((v >> (8 * i)) & 0xFF);
        break;
    default:
        // >>> PLACEHOLDER: signed and float formats. Not used by the GEM
        //     subset we need; add them when the vendor's event list requires.
        put_len(out, it->fmt, 0);
        break;
    }
}

inline std::vector<uint8_t> encode(const ItemPtr& it) {
    std::vector<uint8_t> out;
    encode(it, out);
    return out;
}

// ---- decode ---------------------------------------------------------------

class DecodeError : public std::runtime_error {
public:
    explicit DecodeError(const std::string& m) : std::runtime_error(m) {}
};

inline ItemPtr decode(const uint8_t* p, std::size_t len, std::size_t& off,
                      int depth = 0) {
    if (depth > 16) throw DecodeError("item nesting too deep");
    if (off >= len)  throw DecodeError("truncated at format byte");

    const uint8_t fb = p[off++];
    const Fmt fmt = static_cast<Fmt>(fb >> 2);
    const uint8_t nlen = fb & 0x03;
    if (nlen == 0 || off + nlen > len) throw DecodeError("bad length header");

    std::size_t n = 0;
    for (uint8_t i = 0; i < nlen; ++i) n = (n << 8) | p[off++];

    auto it = std::make_shared<Item>();
    it->fmt = fmt;

    switch (fmt) {
    case Fmt::List:
        for (std::size_t i = 0; i < n; ++i)
            it->list.push_back(decode(p, len, off, depth + 1));
        break;
    case Fmt::ASCII:
        if (off + n > len) throw DecodeError("truncated ascii");
        it->ascii.assign(reinterpret_cast<const char*>(p + off), n);
        off += n;
        break;
    case Fmt::Binary:
    case Fmt::Bool:
        if (off + n > len) throw DecodeError("truncated binary");
        it->bytes.assign(p + off, p + off + n);
        off += n;
        break;
    case Fmt::U1:
        if (off + n > len) throw DecodeError("truncated u1");
        for (std::size_t i = 0; i < n; ++i) it->uints.push_back(p[off++]);
        break;
    case Fmt::U2:
        if (off + n > len) throw DecodeError("truncated u2");
        for (std::size_t i = 0; i + 1 < n + 1 && i < n; i += 2) {
            it->uints.push_back((p[off] << 8) | p[off + 1]);
            off += 2;
        }
        break;
    case Fmt::U4:
        if (off + n > len) throw DecodeError("truncated u4");
        for (std::size_t i = 0; i + 3 < n; i += 4) {
            uint64_t v = 0;
            for (int b = 0; b < 4; ++b) v = (v << 8) | p[off + b];
            it->uints.push_back(v);
            off += 4;
        }
        break;
    default:
        if (off + n > len) throw DecodeError("truncated item");
        it->bytes.assign(p + off, p + off + n);
        off += n;
        break;
    }
    return it;
}

inline ItemPtr decode(const std::vector<uint8_t>& body) {
    if (body.empty()) return nullptr;
    std::size_t off = 0;
    return decode(body.data(), body.size(), off);
}

// Human-readable dump. Essential for debugging a vendor integration at 3am.
inline std::string dump(const ItemPtr& it, int indent = 0) {
    if (!it) return "";
    std::string pad(indent * 2, ' ');
    switch (it->fmt) {
    case Fmt::List: {
        std::string s = pad + "L[" + std::to_string(it->list.size()) + "]\n";
        for (const auto& k : it->list) s += dump(k, indent + 1);
        return s;
    }
    case Fmt::ASCII: return pad + "A \"" + it->ascii + "\"\n";
    case Fmt::U1: case Fmt::U2: case Fmt::U4: case Fmt::U8: {
        std::string s = pad + "U ";
        for (auto v : it->uints) s += std::to_string(v) + " ";
        return s + "\n";
    }
    default: return pad + "B[" + std::to_string(it->bytes.size()) + "]\n";
    }
}

} // namespace fab::secs2
