

use indexmap::IndexMap;
use itertools::Itertools;

use crate::{
    ast::{
        diagnostic::{Span, SpanVariant},
        stmt::{Direction, Stmt, StmtKind},
        visit::{walk_stmt, VisitorMut},
        BinOpKind, Block, DeclKind, Expr, ExprBuilder, ExprKind, Ident, Spanned, TyKind, UnOpKind,
        VarKind,
    },
    proof_rules::ident_to_expr,
    tyctx::TyCtx,
};
#[derive(Debug)]
pub enum PassifyError {
    DirectionMismatch,
    // IdentAlreadyPassified(Ident)
}


#[derive(Clone)]
pub struct Passify<'tcx> {
    tcx: &'tcx TyCtx,
    direction: Direction,
    substitutions: IndexMap<Ident, Ident>,
}

impl<'tcx> Passify<'tcx> {
    pub fn new(tcx: &'tcx TyCtx, direction: Direction) -> Self {
        let substitutions = IndexMap::new();
        Passify {
            tcx,
            direction,
            substitutions,
        }
    }

    fn passify_identifier(&mut self, ident: Ident, span: Span) -> Ident {
        let passified = self.tcx.clone_var(ident, span, VarKind::Subst);
        self.substitutions.insert(ident, passified);
        passified
    }

    fn substitute_in_expr(&self, expr: Expr, span: Span) -> Expr {
        self.filtered_substitute_in_expr(expr, span, |_| true)
    }

    fn filtered_substitute_in_expr<F>(&self, expr: Expr, span: Span, filter_idents: F) -> Expr
    where
        F: Fn(Ident) -> bool,
    {
        let builder = ExprBuilder::new(span);
        builder.subst(
            expr,
            self.substitutions
                .iter()
                .filter(|(k, _)| filter_idents(**k))
                .map(|(k, v)| (*k, ident_to_expr(self.tcx, span, *v))),
        )
    }

    fn build_havoc_assume(
        &mut self,
        span: Span,
        assume_lhs: Ident,
        assume_rhs: &Expr,
    ) -> Vec<Spanned<StmtKind>> {
        let expr_builder = ExprBuilder::new(span);
        let passified_equals_expr = expr_builder.binary(
            BinOpKind::Eq,
            Some(TyKind::Bool),
            ident_to_expr(self.tcx, span, assume_lhs),
            assume_rhs.clone(),
        );
        let mut embedded_passified_equals_expr =
            expr_builder.unary(UnOpKind::Embed, Some(TyKind::EUReal), passified_equals_expr);
        if self.direction == Direction::Up {
            embedded_passified_equals_expr = expr_builder.unary(
                UnOpKind::Not,
                Some(TyKind::EUReal),
                embedded_passified_equals_expr,
            )
        }
        vec![
            Spanned::new(span, StmtKind::Havoc(self.direction, vec![assume_lhs])),
            Spanned::new(
                span,
                StmtKind::Assume(self.direction, embedded_passified_equals_expr),
            ),
        ]
    }

    // Builds a passified version of the assignment if possible.
    fn encode_assign(&mut self, span: Span, ident: Ident, expr: &Expr) -> Option<Block> {
        if let ExprKind::Call(call_ident, _) = &expr.kind {
            match self
                .tcx
                .get(*call_ident)
                .expect("whatever this called needs to have been registered")
                .as_ref()
            {
                DeclKind::FuncIntrin(_) => {}
                DeclKind::ProcIntrin(_) => {}
                decl_kind => {
                    unreachable!(
                        "Everything but intrinsics should have been desugared, got a {:?}",
                        decl_kind
                    )
                }
            }
            // TODO: These need special handling which I'll have to think about later on
            return None;
        }

        let span = span.variant(SpanVariant::Passify);
        // Order of these two is important as we want the newly passified var to not appear in the subst
        let passified_expr = &self.substitute_in_expr(expr.to_owned(), span);
        let passified_ident = self.passify_identifier(ident, span);
        let havoc_assume = self.build_havoc_assume(span, passified_ident, passified_expr);
        Some(Spanned::new(span, havoc_assume))
    }

    fn visit_branching_stmt(
        &mut self,
        enclosing_span: Span,
        branch_a: &mut Block,
        branch_b: &mut Block,
    ) -> Result<(), <Passify<'tcx> as VisitorMut>::Err> {
        // TODO revisit which span I use where
        let mut pass_branch_a = self.clone();
        pass_branch_a.visit_block(branch_a)?;
        let mut pass_branch_b = self.clone();
        pass_branch_b.visit_block(branch_b)?;

        // find the vars that were freshly passified inside the two branches
        let substitution_diff = (pass_branch_a.substitutions.keys())
            .chain(pass_branch_b.substitutions.keys())
            .unique()
            .filter(|k| {
                match (
                    pass_branch_a.substitutions.get(*k),
                    pass_branch_b.substitutions.get(*k),
                ) {
                    (Some(a_v), Some(b_v)) => a_v != b_v,
                    (None, None) => unreachable!(),
                    _ => false, // TODO: should this actually be false? we might be assuming vars that are not in scope
                }
            })
            .copied()
            .collect_vec();
        // TODO assert that the diff contains no keys of self.substitutions

        // create passified variables to join the branch vars with
        for differing_var in &substitution_diff {
            self.passify_identifier(differing_var.clone(), enclosing_span);
        }
        for (branch, mut pass_branch) in [(branch_a, pass_branch_a), (branch_b, pass_branch_b)] {
            let branch_span = branch.span.variant(SpanVariant::Passify);
            for differing_ident in &substitution_diff {
                let passified = (self.substitutions.get(differing_ident))
                    .expect("these should be preassigned from the loop above");
                let ident_in_branch = (pass_branch.substitutions.get(differing_ident))
                    .expect("we are operating on the joined set, this should exist");
                branch.node.extend(pass_branch.build_havoc_assume(
                    branch_span,
                    passified.clone(),
                    // TODO: I removed the substitution wrapping here and I am pretty sure that is correct, but I need to verify
                    &ident_to_expr(&pass_branch.tcx, branch_span, ident_in_branch.clone()),
                ));
            }
        }
        Ok(())
    }
}

impl<'tcx> VisitorMut for Passify<'tcx> {
    type Err = PassifyError;

    fn visit_stmt(&mut self, s: &mut Stmt) -> Result<(), Self::Err> {
        if !matches!(
            s.node,
            StmtKind::Demonic(..) | StmtKind::Angelic(..) | StmtKind::If(..)
        ) {
            walk_stmt(self, s)?;
        }
        match &mut s.node {
            StmtKind::Assign(idents, expr) => {
                let [ident] = idents.as_slice() else {
                    unreachable!("SpecCall desugaring should have removed all tuple lhses");
                };
                if let Some(encoding) = self.encode_assign(s.span, *ident, expr) {
                    s.node = StmtKind::Seq(encoding.node);
                };
            }
            StmtKind::Var(decl_ref) => {
                let declaration = decl_ref.borrow();
                let Some(rhs) = &declaration.init else {
                    return Ok(());
                };
                let Some(mut block) = self.encode_assign(s.span, declaration.name, rhs) else {
                    return Ok(());
                };
                drop(declaration);
                {
                    let mut declaration = decl_ref.borrow_mut();
                    declaration.init = None;
                }

                block
                    .node
                    .insert(0, Spanned::new(block.span, StmtKind::Var(decl_ref.clone())));
                s.span = block.span;
                s.node = StmtKind::Seq(block.node);
            }
            StmtKind::Angelic(..) | StmtKind::Demonic(..) => {
                let (nondet_direction, branch_a, branch_b) = match &mut s.node {
                    StmtKind::Angelic(ref mut a, ref mut b) => (Direction::Up, a, b),
                    StmtKind::Demonic(ref mut a, ref mut b) => (Direction::Down, a, b),
                    _ => unreachable!(),
                };
                if nondet_direction != self.direction {
                    return Err(PassifyError::DirectionMismatch);
                }
                self.visit_branching_stmt(
                    s.span.variant(SpanVariant::Passify),
                    branch_a,
                    branch_b,
                )?;
            }
            StmtKind::Assert(direction, expr) => {
                s.node = StmtKind::Assert(
                    *direction,
                    self.substitute_in_expr(expr.clone(), s.span.variant(SpanVariant::Passify)),
                )
            }
            StmtKind::Assume(direction, expr) => {
                s.node = StmtKind::Assume(
                    *direction,
                    self.substitute_in_expr(expr.clone(), s.span.variant(SpanVariant::Passify)),
                )
            }
            StmtKind::Havoc(direction, idents) => {
                s.node = StmtKind::Havoc(
                    *direction,
                    idents
                        .clone()
                        .iter()
                        .map(|ident| *self.substitutions.get(ident).unwrap_or(ident))
                        .collect(),
                )
            }
            StmtKind::Tick(expr) => {
                s.node = StmtKind::Tick(
                    self.substitute_in_expr(expr.clone(), s.span.variant(SpanVariant::Passify)),
                )
            }
            StmtKind::If(expr, branch_a, branch_b) => {
                let passified_condition =
                    self.substitute_in_expr(expr.clone(), s.span.variant(SpanVariant::Passify));
                self.visit_branching_stmt(s.span, branch_a, branch_b)?;
                s.node = StmtKind::If(
                    passified_condition,
                    branch_a.to_owned(),
                    branch_b.to_owned(),
                );
            }
            StmtKind::Compare(_, expr) => {
                s.node = StmtKind::Compare(
                    self.direction,
                    self.substitute_in_expr(expr.to_owned(), s.span.variant(SpanVariant::Passify)),
                )
            }
            StmtKind::Weigh(expr) => {
                s.node = StmtKind::Tick(
                    self.substitute_in_expr(expr.to_owned(), s.span.variant(SpanVariant::Passify)),
                )
            }
            StmtKind::While(expr, block) => {
                s.node = StmtKind::While(
                    self.substitute_in_expr(expr.to_owned(), s.span.variant(SpanVariant::Passify)),
                    block.clone(),
                );
            }
            _ => {}
        };
        Ok(())
    }
}

// return sone Span:variant(SpanVariant::Passify)
