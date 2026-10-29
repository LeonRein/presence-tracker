//! JSON as Python's json.dumps writes it (separators ", " and ": ", floats as repr, ensure_ascii): the
//! trace of presence-replay is byte for byte the one rust-port/trace.py writes through the Python shell.
//! serde_json writes numbers otherwise (1e-5 for 1e-05) and no NaN.

use std::fmt::Write;

/// A value json.dumps would write.
pub trait PyJson {
    fn write(&self, out: &mut String);
}

/// Writes an object field by field, in order (a Python dict keeps its keys as inserted); `end` closes it.
pub struct Fields<'o> {
    out: &'o mut String,
    first: bool,
}

impl<'o> Fields<'o> {
    pub fn new(out: &'o mut String) -> Self {
        out.push('{');
        Self { out, first: true }
    }

    pub fn field(mut self, key: &str, value: &dyn PyJson) -> Self {
        if !self.first {
            self.out.push_str(", ");
        }
        self.first = false;
        key.write(self.out);
        self.out.push_str(": ");
        value.write(self.out);
        self
    }

    pub fn end(self) {
        self.out.push('}');
    }
}

/// A dict of string keys to values of one kind, in order.
pub struct Dict<'a, V>(pub Vec<(&'a str, V)>);

impl<V: PyJson> PyJson for Dict<'_, V> {
    fn write(&self, out: &mut String) {
        self.0.iter().fold(Fields::new(out), |f, (k, v)| f.field(k, v)).end();
    }
}

impl<T: PyJson> PyJson for [T] {
    fn write(&self, out: &mut String) {
        out.push('[');
        for (k, v) in self.iter().enumerate() {
            if k > 0 {
                out.push_str(", ");
            }
            v.write(out);
        }
        out.push(']');
    }
}

impl<T: PyJson> PyJson for Vec<T> {
    fn write(&self, out: &mut String) {
        self.as_slice().write(out);
    }
}

impl<T: PyJson> PyJson for Option<T> {
    fn write(&self, out: &mut String) {
        match self {
            Some(v) => v.write(out),
            None => out.push_str("null"),
        }
    }
}

impl PyJson for bool {
    fn write(&self, out: &mut String) {
        out.push_str(if *self { "true" } else { "false" });
    }
}

impl PyJson for usize {
    fn write(&self, out: &mut String) {
        write!(out, "{self}").expect("a String takes it");
    }
}

impl PyJson for str {
    /// json.dumps' escapes with ensure_ascii: \" \\ \n \r \t \b \f, other controls and all non-ASCII as \uXXXX
    /// (beyond the BMP as a surrogate pair).
    fn write(&self, out: &mut String) {
        out.push('"');
        for c in self.chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                '\u{8}' => out.push_str("\\b"),
                '\u{c}' => out.push_str("\\f"),
                ' '..='~' => out.push(c),
                _ => {
                    let mut units = [0u16; 2];
                    for u in c.encode_utf16(&mut units) {
                        write!(out, "\\u{u:04x}").expect("a String takes it");
                    }
                }
            }
        }
        out.push('"');
    }
}

impl PyJson for &str {
    fn write(&self, out: &mut String) {
        (**self).write(out);
    }
}

impl PyJson for String {
    fn write(&self, out: &mut String) {
        self.as_str().write(out);
    }
}

impl PyJson for f64 {
    /// float.__repr__: the shortest digits that read back the same (Rust's too), in positional notation for
    /// decimal exponents -4..16 (with ".0" if whole), else d.ddde+XX with at least two exponent digits;
    /// json.dumps' NaN, Infinity, -Infinity.
    fn write(&self, out: &mut String) {
        let x = *self;
        if x.is_nan() {
            return out.push_str("NaN");
        }
        if x.is_infinite() {
            return out.push_str(if x > 0.0 { "Infinity" } else { "-Infinity" });
        }
        // as many digits as the shortest that reads back the same ({:e}, e.g. -1.2345e-5), but rounded
        // exactly to that many ({:.*e}): at a tie (…390625 to 17 digits) Python rounds to even, the
        // shortest form up
        let shortest = format!("{x:e}");
        let n = shortest
            .split_once('e')
            .expect("{:e} has an exponent")
            .0
            .chars()
            .filter(char::is_ascii_digit)
            .count();
        let sci = format!("{x:.*e}", n - 1);
        let (mantissa, exp) = sci.split_once('e').expect("{:e} has an exponent");
        let exp: i32 = exp.parse().expect("an integer exponent");
        let (sign, mantissa) = mantissa.strip_prefix('-').map_or(("", mantissa), |m| ("-", m));
        let digits: String = mantissa.chars().filter(|&c| c != '.').collect();
        out.push_str(sign);
        if (-4..16).contains(&exp) {
            let n = digits.len() as i32;
            if exp < 0 {
                out.push_str("0.");
                out.extend(std::iter::repeat_n('0', (-exp - 1) as usize));
                out.push_str(&digits);
            } else if n > exp + 1 {
                let (int, frac) = digits.split_at((exp + 1) as usize);
                write!(out, "{int}.{frac}").expect("a String takes it");
            } else {
                out.push_str(&digits);
                out.extend(std::iter::repeat_n('0', (exp + 1 - n) as usize));
                out.push_str(".0");
            }
        } else {
            let (first, rest) = digits.split_at(1);
            out.push_str(first);
            if !rest.is_empty() {
                write!(out, ".{rest}").expect("a String takes it");
            }
            write!(out, "e{}{:02}", if exp < 0 { '-' } else { '+' }, exp.abs()).expect("a String takes it");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dumps(v: &dyn PyJson) -> String {
        let mut out = String::new();
        v.write(&mut out);
        out
    }

    #[test]
    fn floats_are_written_as_pythons_repr() {
        // repr() of these in CPython 3.13
        for (x, py) in [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (0.1, "0.1"),
            (-2.5, "-2.5"),
            (100.0, "100.0"),
            (123456.789, "123456.789"),
            (0.0001, "0.0001"),
            (0.00012345, "0.00012345"),
            (1e-5, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (1e15, "1000000000000000.0"),
            (9999999999999998.0, "9999999999999998.0"),
            (1e16, "1e+16"),
            (1.5e16, "1.5e+16"),
            (1e22, "1e+22"),
            (5e-324, "5e-324"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
            (1.0 / 3.0, "0.3333333333333333"),
            (2.0 / 3.0, "0.6666666666666666"),
            (1259.6870727539062, "1259.6870727539062"), // a tie at the 17th digit: to even
            (3334.1389770507812, "3334.1389770507812"),
            (f64::NAN, "NaN"),
            (f64::INFINITY, "Infinity"),
            (f64::NEG_INFINITY, "-Infinity"),
        ] {
            assert_eq!(dumps(&x), py, "{x:e}");
        }
    }

    #[test]
    fn objects_lists_and_strings_are_written_as_json_dumps() {
        let w = vec![0.5, 1e-05];
        let missing: Option<f64> = None;
        let rows = Dict(vec![("a", vec![1.0, 0.0])]);
        let mut out = String::new();
        Fields::new(&mut out)
            .field("t", &1.25)
            .field("n", &3usize)
            .field("w", &w)
            .field("none", &missing)
            .field("on", &true)
            .field("rows", &rows)
            .field("s", &"Küche \"1\"\n😀")
            .end();
        assert_eq!(
            out,
            r#"{"t": 1.25, "n": 3, "w": [0.5, 1e-05], "none": null, "on": true, "rows": {"a": [1.0, 0.0]}, "s": "K\u00fcche \"1\"\n\ud83d\ude00"}"#
        );
    }
}
