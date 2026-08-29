#pragma once
// fab/json.hpp — a small, dependency-free JSON reader.
//
// Deliberately minimal: enough to load a tool master file, nothing more.
// >>> PLACEHOLDER: swap for nlohmann/json or simdjson when you add a package
//     manager. The Value API below is intentionally shaped like nlohmann's
//     so the call sites in tool_factory.hpp survive the migration unchanged.

#include <cctype>
#include <cstdlib>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace fab::json {

class Value;
using Object = std::map<std::string, Value>;
using Array  = std::vector<Value>;

class Value {
public:
    enum class Type { Null, Bool, Number, String, Array, Object };

    Value() = default;
    Value(bool b)                : type_(Type::Bool),   bool_(b) {}
    Value(double d)              : type_(Type::Number), num_(d) {}
    Value(std::string s)         : type_(Type::String), str_(std::move(s)) {}
    Value(Array a)               : type_(Type::Array),  arr_(std::move(a)) {}
    Value(Object o)              : type_(Type::Object), obj_(std::move(o)) {}

    Type type() const { return type_; }
    bool is_null()   const { return type_ == Type::Null; }
    bool is_array()  const { return type_ == Type::Array; }
    bool is_object() const { return type_ == Type::Object; }

    // Accessors with defaults — config files are full of optional fields.
    bool        as_bool(bool d = false)          const { return type_ == Type::Bool   ? bool_ : d; }
    double      as_double(double d = 0.0)        const { return type_ == Type::Number ? num_  : d; }
    int         as_int(int d = 0)                const { return type_ == Type::Number ? (int)num_ : d; }
    std::string as_string(std::string d = "")    const { return type_ == Type::String ? str_  : d; }

    const Array&  as_array()  const { return arr_; }
    const Object& as_object() const { return obj_; }

    // obj["key"] — returns a null Value if absent, so chains never throw.
    const Value& operator[](const std::string& k) const {
        static const Value kNull;
        if (type_ != Type::Object) return kNull;
        auto it = obj_.find(k);
        return it == obj_.end() ? kNull : it->second;
    }

    bool contains(const std::string& k) const {
        return type_ == Type::Object && obj_.count(k) > 0;
    }

    std::vector<std::string> string_list() const {
        std::vector<std::string> v;
        for (const auto& e : arr_) v.push_back(e.as_string());
        return v;
    }

private:
    Type        type_ = Type::Null;
    bool        bool_ = false;
    double      num_  = 0.0;
    std::string str_;
    Array       arr_;
    Object      obj_;
};

class Parser {
public:
    static Value parse(const std::string& text) {
        Parser p(text);
        p.ws();
        Value v = p.value();
        p.ws();
        if (p.i_ != p.s_.size()) p.fail("trailing content");
        return v;
    }

private:
    explicit Parser(const std::string& s) : s_(s) {}

    [[noreturn]] void fail(const std::string& msg) const {
        throw std::runtime_error("json: " + msg + " at offset " +
                                 std::to_string(i_));
    }

    void ws() {
        while (i_ < s_.size() && std::isspace((unsigned char)s_[i_])) ++i_;
    }

    char peek() const { return i_ < s_.size() ? s_[i_] : '\0'; }

    void expect(char c) {
        if (peek() != c) fail(std::string("expected '") + c + "'");
        ++i_;
    }

    Value value() {
        ws();
        switch (peek()) {
            case '{': return object();
            case '[': return array();
            case '"': return Value(string());
            case 't': lit("true");  return Value(true);
            case 'f': lit("false"); return Value(false);
            case 'n': lit("null");  return Value();
            default:  return Value(number());
        }
    }

    void lit(const char* w) {
        const std::size_t n = std::string(w).size();
        if (s_.compare(i_, n, w) != 0) fail(std::string("expected ") + w);
        i_ += n;
    }

    Value object() {
        expect('{');
        Object o;
        ws();
        if (peek() == '}') { ++i_; return Value(std::move(o)); }
        for (;;) {
            ws();
            std::string k = string();
            ws();
            expect(':');
            o[k] = value();
            ws();
            if (peek() == ',') { ++i_; continue; }
            expect('}');
            break;
        }
        return Value(std::move(o));
    }

    Value array() {
        expect('[');
        Array a;
        ws();
        if (peek() == ']') { ++i_; return Value(std::move(a)); }
        for (;;) {
            a.push_back(value());
            ws();
            if (peek() == ',') { ++i_; continue; }
            expect(']');
            break;
        }
        return Value(std::move(a));
    }

    std::string string() {
        expect('"');
        std::string out;
        while (i_ < s_.size() && s_[i_] != '"') {
            if (s_[i_] == '\\') {
                ++i_;
                if (i_ >= s_.size()) fail("bad escape");
                switch (s_[i_]) {
                    case 'n': out += '\n'; break;
                    case 't': out += '\t'; break;
                    case 'r': out += '\r'; break;
                    case 'b': out += '\b'; break;
                    case 'f': out += '\f'; break;
                    case '/': out += '/';  break;
                    case '\\': out += '\\'; break;
                    case '"': out += '"';  break;
                    // >>> PLACEHOLDER: \uXXXX not handled. Tool IDs are ASCII.
                    default: fail("unsupported escape");
                }
                ++i_;
            } else {
                out += s_[i_++];
            }
        }
        expect('"');
        return out;
    }

    double number() {
        const char* start = s_.c_str() + i_;
        char* end = nullptr;
        const double d = std::strtod(start, &end);
        if (end == start) fail("bad number");
        i_ += (end - start);
        return d;
    }

    const std::string& s_;
    std::size_t        i_ = 0;
};

inline Value parse(const std::string& text) { return Parser::parse(text); }

} // namespace fab::json
