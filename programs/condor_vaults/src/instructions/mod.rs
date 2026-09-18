//! One file per instruction (or per small family of them that share an account
//! struct), so a handler is read beside the accounts it is allowed to touch.

pub mod collect_seed;
pub mod create_vault;
pub mod income;
pub mod install_delegate;
pub mod protocol;
pub mod redeem;
pub mod strategy;
pub mod tokenize;
pub mod treasury;
pub mod wind_down;

pub use collect_seed::*;
pub use create_vault::*;
pub use income::*;
pub use install_delegate::*;
pub use protocol::*;
pub use redeem::*;
pub use strategy::*;
pub use tokenize::*;
pub use treasury::*;
pub use wind_down::*;
