//! A pool of objects shared between hypotheses, with indices that stay valid while the object lives
//! (docs/rust-core.md 2.1): hypotheses hold indices, never the objects.

use std::marker::PhantomData;

/// The index type of a pool's objects.
pub trait PoolId: Copy {
    fn from_index(index: usize) -> Self;
    fn index(self) -> usize;
}

#[derive(Debug)]
pub struct Pool<T, I> {
    slots: Vec<Option<T>>,
    free: Vec<usize>,
    id: PhantomData<I>,
}

impl<T, I> Default for Pool<T, I> {
    fn default() -> Self {
        Self {
            slots: Vec::new(),
            free: Vec::new(),
            id: PhantomData,
        }
    }
}

impl<T, I: PoolId> Pool<T, I> {
    pub fn insert(&mut self, value: T) -> I {
        match self.free.pop() {
            Some(index) => {
                self.slots[index] = Some(value);
                I::from_index(index)
            }
            None => {
                self.slots.push(Some(value));
                I::from_index(self.slots.len() - 1)
            }
        }
    }

    /// Panics if the object was released.
    pub(super) fn remove(&mut self, id: I) -> T {
        let value = self.slots[id.index()].take().expect("an object released twice");
        self.free.push(id.index());
        value
    }

    pub fn contains(&self, id: I) -> bool {
        self.slots.get(id.index()).is_some_and(Option::is_some)
    }

    pub fn get(&self, id: I) -> &T {
        self.slots[id.index()]
            .as_ref()
            .expect("an object used after its release")
    }

    pub fn get_mut(&mut self, id: I) -> &mut T {
        self.slots[id.index()]
            .as_mut()
            .expect("an object used after its release")
    }

    pub fn len(&self) -> usize {
        self.slots.len() - self.free.len()
    }

    /// One more than the largest index ever given (for tables by index).
    pub fn capacity(&self) -> usize {
        self.slots.len()
    }

    /// The live objects' indices, in the order of the indices.
    pub fn ids(&self) -> impl Iterator<Item = I> + '_ {
        self.slots
            .iter()
            .enumerate()
            .filter(|(_, s)| s.is_some())
            .map(|(i, _)| I::from_index(i))
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Debug, Clone, Copy, PartialEq)]
    struct Id(usize);

    impl PoolId for Id {
        fn from_index(index: usize) -> Self {
            Id(index)
        }
        fn index(self) -> usize {
            self.0
        }
    }

    #[test]
    fn released_slots_are_taken_again() {
        let mut pool: Pool<&str, Id> = Pool::default();
        let a = pool.insert("a");
        let b = pool.insert("b");
        assert_eq!(pool.remove(a), "a");
        let c = pool.insert("c");
        assert_eq!(c, a);
        assert_eq!(*pool.get(b), "b");
        assert_eq!(pool.len(), 2);
    }
}
