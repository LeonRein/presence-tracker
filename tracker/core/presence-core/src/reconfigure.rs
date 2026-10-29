//! Which way a new configuration takes while the model runs (Tracker.reconfigure, docs/rust-core.md 4):
//! only what the outputs read changed (nothing is built), only sensors changed and the regions stay (a
//! new world the people may live in: recalibrated, turned, moved, switched on or off), or anything else
//! (the model starts over in a new world).

use serde_json::{Map, Value};

use crate::config::{Config, ConfigError};
use crate::params::OUTPUT_FIELDS;
use crate::plan::Plan;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Change {
    /// only what the outputs read (OUTPUT_FIELDS), the names of zones and sensors the same
    Outputs,
    /// only these sensors (ids; perhaps none, then names) changed, the same sensors and regions
    Sensors(Vec<String>),
    /// walls, rooms, doors, the model's parameters, the set of sensors or the regions
    World,
}

/// Equal as Python's == of JSON values: numbers by value (1 == 1.0, true == 1), objects whatever the order
/// of their keys.
fn same(a: &Value, b: &Value) -> bool {
    let number = |v: &Value| match v {
        Value::Bool(x) => Some(f64::from(u8::from(*x))),
        Value::Number(n) => n.as_f64(),
        _ => None,
    };
    match (a, b) {
        (Value::Array(x), Value::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(x, y)| same(x, y)),
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| same(v, w)))
        }
        _ => match (number(a), number(b)) {
            (Some(x), Some(y)) => x == y,
            _ => a == b,
        },
    }
}

fn object(v: &Value) -> Map<String, Value> {
    v.as_object().cloned().unwrap_or_default()
}

fn list(v: Option<&Value>) -> Vec<Value> {
    v.and_then(Value::as_array).cloned().unwrap_or_default()
}

fn text(v: &Value, key: &str) -> Value {
    v.get(key).cloned().unwrap_or(Value::Null)
}

/// The regions of a plan without their names: id, rooms, open, exits.
fn regions(plan: &Plan) -> Vec<(String, Vec<&str>, bool, usize)> {
    let mut r: Vec<_> = plan
        .regions
        .iter()
        .map(|r| {
            let rooms = r.rooms.iter().map(|z| plan.zones[z.0].id.as_str()).collect();
            (r.id.clone(), rooms, r.open(), r.exits)
        })
        .collect();
    r.sort_by(|a, b| a.0.cmp(&b.0));
    r
}

/// The way from the configuration `old` to `new` (both the JSON the shell stores; ConfigError if one
/// cannot be read).
pub fn change(old: &str, new: &str) -> Result<Change, ConfigError> {
    let parse = |json: &str| -> Result<(Value, Plan), ConfigError> {
        let plan = Plan::new(&Config::from_json(json)?);
        Ok((serde_json::from_str(json).map_err(ConfigError::unreadable)?, plan))
    };
    let ((a, plan_a), (b, plan_b)) = (parse(old)?, parse(new)?);
    let (sa, sb) = (list(a.get("sensors")), list(b.get("sensors")));
    // what the model is built of: all but the sensors, what only the outputs read and what the zones are
    // called (the background image is the web UI's: the shell never hands it over)
    let rest = |v: &Value| {
        let mut o = object(v);
        o.remove("sensors");
        if let Some(Value::Object(params)) = o.get_mut("params") {
            for k in OUTPUT_FIELDS {
                params.remove(k);
            }
        }
        if let Some(Value::Array(zones)) = o.get_mut("zones") {
            for z in zones.iter_mut().filter_map(Value::as_object_mut) {
                z.remove("name");
            }
        }
        Value::Object(o)
    };
    let ids = |s: &[Value]| s.iter().map(|x| text(x, "id")).collect::<Vec<_>>();
    if !same(&rest(&a), &rest(&b)) || ids(&sa) != ids(&sb) || regions(&plan_a) != regions(&plan_b) {
        return Ok(Change::World);
    }
    let unnamed = |s: &Value| {
        let mut o = object(s);
        o.remove("name");
        Value::Object(o)
    };
    let changed: Vec<String> = sa
        .iter()
        .zip(&sb)
        .filter(|(x, y)| !same(&unnamed(x), &unnamed(y)))
        .map(|(x, _)| x.get("id").and_then(Value::as_str).unwrap_or_default().to_string())
        .collect();
    // the plan reads the names (a sensor's own room by name): a new name goes the way of the sensors
    let names = |v: &Value, s: &[Value]| {
        let zones: Vec<Value> = list(v.get("zones")).iter().map(|z| text(z, "name")).collect();
        (zones, s.iter().map(|x| text(x, "name")).collect::<Vec<_>>())
    };
    Ok(if changed.is_empty() && names(&a, &sa) == names(&b, &sb) {
        Change::Outputs
    } else {
        Change::Sensors(changed)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing;

    #[test]
    fn the_way_of_a_new_configuration() {
        let old = testing::config_json(&[("s", "", [0.5, 0.5], 0.0)], "[]", "[]", "[]");
        assert_eq!(change(&old, &old), Ok(Change::Outputs));
        let light = old.replace(r#""light_cost": 2.0"#, r#""light_cost": 3"#);
        assert_ne!(light, old);
        assert_eq!(change(&old, &light), Ok(Change::Outputs));
        let turned = old.replace(r#""heading": 0"#, r#""heading": 10"#);
        assert_ne!(turned, old);
        assert_eq!(change(&old, &turned), Ok(Change::Sensors(vec!["s".into()])));
        let renamed = old.replace(r#""name": """#, r#""name": "Bad""#);
        assert_eq!(change(&old, &renamed), Ok(Change::Sensors(Vec::new())));
        let walls = old.replace(
            r#""walls": []"#,
            &format!("\"walls\": [{}]", testing::wall([0.0, 0.0], [1.0, 0.0])),
        );
        assert_eq!(change(&old, &walls), Ok(Change::World));
        // 1 and 1.0 are the same number
        assert_eq!(
            change(&old, &old.replace(r#""x": 0.5"#, r#""x": 0.50"#)),
            Ok(Change::Outputs)
        );
    }
}
