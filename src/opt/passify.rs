use std::rc::Rc;

use indexmap::IndexMap;
use itertools::Itertools;

use crate::{
    ast::{
        diagnostic::{Span, SpanVariant},
        stmt::{Direction, Stmt, StmtKind},
        visit::{walk_stmt, VisitorMut},
        BinOpKind, Block, DeclKind, Expr, ExprBuilder, ExprData, ExprKind, Ident, Shared, Spanned,
        TyKind, UnOpKind, VarKind,
    },
    intrinsic::{
        distributions::{Dist, DistributionProc},
        ProcIntrin,
    },
    proof_rules::ident_to_expr,
    tyctx::TyCtx,
};
#[derive(Debug)]
pub enum PassifyError {
    DirectionMismatch,
    NotYetSupported, // IdentAlreadyPassified(Ident)
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
    ) -> [Spanned<StmtKind>; 3] {
        let expr_builder = ExprBuilder::new(span);
        let passified_equals_expr = expr_builder.binary(
            BinOpKind::Eq,
            Some(TyKind::Bool),
            ident_to_expr(self.tcx, span, assume_lhs),
            assume_rhs.clone(),
        );
        let mut embedded_passified_equals_expr = expr_builder.unary(
            UnOpKind::Embed,
            Some(self.tcx.spec_ty().clone()),
            passified_equals_expr,
        );
        if self.direction == Direction::Up {
            embedded_passified_equals_expr = expr_builder.unary(
                UnOpKind::Not,
                Some(self.tcx.spec_ty().clone()),
                embedded_passified_equals_expr,
            )
        }
        // let DeclKind::VarDecl(passified_var_decl) =
        //     self.tcx.get(assume_lhs).unwrap().as_ref().clone()
        // else {
        //     unreachable!()
        // };
        // expr_builder.var(assume_lhs, self.tcx));

        let decl = self.tcx.get(assume_lhs).unwrap();
        let var_decl_lhs = match decl.as_ref() {
            DeclKind::VarDecl(var_decl) => var_decl,
            _ => panic!("expected variable declaration"),
        };
        [
            Spanned::new(span, StmtKind::Var(var_decl_lhs.clone(), None)),
            Spanned::new(span, StmtKind::Havoc(self.direction, vec![assume_lhs])),
            Spanned::new(
                span,
                StmtKind::Assume(self.direction, embedded_passified_equals_expr),
            ),
        ]
    }

    fn encode_intrinsic_proc(
        &mut self,
        assign_to: Ident,
        span: Span,
        proc_name: Rc<dyn ProcIntrin>,
        args: &[Expr],
    ) -> Result<Block, PassifyError> {
        let proc = proc_name
            .as_any_rc()
            .downcast::<DistributionProc>()
            .unwrap();
        match proc.name().name.to_owned().as_str() {
            "flip" => {
                let span = span.variant(SpanVariant::Passify);
                let expr_builder = ExprBuilder::new(span);
                let Dist(outcomes) = (proc.apply)(args, expr_builder);
                let branches: Vec<Block> = outcomes
                    .into_iter()
                    .map(|(probability, value)| {
                        let probability = self.substitute_in_expr(probability, span);
                        let value = &self.substitute_in_expr(value, span);
                        let [_, havoc, assume] = self.build_havoc_assume(span, assign_to, value);
                        Spanned::new(
                            span,
                            vec![
                                Spanned::new(span, StmtKind::Weigh(probability)),
                                havoc,
                                assume,
                            ],
                        )
                    })
                    .collect();
                let folded: Option<Spanned<Vec<Spanned<StmtKind>>>> =
                    branches.into_iter().reduce(|acc, new_branch| {
                        Spanned::new(
                            span,
                            vec![Spanned::new(span, StmtKind::Additive(acc, new_branch))],
                        )
                    });
                Ok(folded.expect("each distribution should have at least two values"))
            }
            _ => return Err(PassifyError::NotYetSupported),
        }
    }

    // Builds a passified version of the assignment if possible.
    fn encode_assign(
        &mut self,
        span: Span,
        ident: Ident,
        expr: &Expr,
    ) -> Result<Block, PassifyError> {
        match self.tcx.get_var_ty(ident) {
            Some(TyKind::List(..)) => return Err(PassifyError::NotYetSupported),
            Some(..) => {}
            None => panic!(),
        };
        if let ExprKind::Call(call_ident, args) = &expr.kind {
            match self
                .tcx
                .get(*call_ident)
                .expect("whatever this called needs to have been registered")
                .as_ref()
            {
                DeclKind::FuncIntrin(_) => {}
                DeclKind::FuncDecl(..) => {}
                DeclKind::ProcIntrin(proc) => {
                    let args = &args
                        .iter()
                        .map(|arg| match arg.kind {
                            ExprKind::Lit(_) => arg.clone(),
                            _ => self.substitute_in_expr(arg.clone(), span),
                        })
                        .collect_vec();
                    let passified_ident = self.passify_identifier(ident, span);
                    // return self.encode_intrinsic_proc(passified_ident, span, proc.clone(), args);
                    // let builder = ExprBuilder::new(span);
                    let new_rhs = Shared::new(ExprData {
                        kind: ExprKind::Call(*call_ident, args.clone()),
                        ty: expr.ty.clone(),
                        span,
                    });
                    return Ok(Spanned::new(
                        span,
                        vec![Spanned::new(
                            span,
                            StmtKind::Assign(vec![passified_ident], new_rhs),
                        )],
                    ));
                }
                decl_kind => {
                    unreachable!(
                        "Everything but intrinsics should have been desugared, got a {:?}",
                        decl_kind
                    )
                }
            }

            // TODO: These need special handling which I'll have to think about later on
            return Err(PassifyError::NotYetSupported);
        }

        let span = span.variant(SpanVariant::Passify);
        // Order of these two is important as we want the newly passified var to not appear in the subst
        let passified_expr = &self.substitute_in_expr(expr.to_owned(), span);
        let passified_ident = self.passify_identifier(ident, span);
        let havoc_assume = self.build_havoc_assume(span, passified_ident, passified_expr);
        Ok(Spanned::new(span, havoc_assume.to_vec()))
    }

    fn visit_branching_stmt(
        &mut self,
        enclosing_span: Span,
        branch_a: &mut Block,
        branch_b: &mut Block,
    ) -> Result<(), <Passify<'tcx> as VisitorMut>::Err> {
        // TODO revisit which span I use where
        let mut pass_branch_a = self.clone();
        // println!("branch_a:\n{}", branch_a);
        pass_branch_a.visit_block(branch_a)?;
        // println!("branch_a:\n{}", branch_a);
        // println!("{:?}", pass_branch_a.substitutions);
        let mut pass_branch_b = self.clone();
        pass_branch_b.visit_block(branch_b)?;

        // find the vars that were freshly passified inside the two branches
        let substitution_diff = (pass_branch_a.substitutions.keys())
            .chain(pass_branch_b.substitutions.keys())
            .unique()
            .filter(|k| pass_branch_a.substitutions.get(*k) != pass_branch_b.substitutions.get(*k))
            .copied()
            .collect_vec();

        // create passified variables to join the branch vars with
        for differing_var in &substitution_diff {
            self.passify_identifier(differing_var.clone(), enclosing_span);
        }
        // println!("{:?}", self.substitutions);
        for (branch, mut pass_branch) in [(branch_a, pass_branch_a), (branch_b, pass_branch_b)] {
            let branch_span = branch.span.variant(SpanVariant::Passify);
            let joining_blocks = substitution_diff
                .iter()
                .map(|differing_ident| {
                    let ident_in_branch = pass_branch
                        .substitutions
                        .get(differing_ident)
                        .copied()
                        .unwrap_or(*differing_ident);
                    let passified = (self.substitutions.get(differing_ident))
                        .expect("these should be preassigned from the loop above");
                    pass_branch.build_havoc_assume(
                        branch_span,
                        passified.clone(),
                        // TODO: I removed the substitution wrapping here and I am pretty sure that is correct, but I need to verify
                        &ident_to_expr(&pass_branch.tcx, branch_span, ident_in_branch.clone()),
                    )
                })
                .flatten()
                .collect_vec();
            branch
                .node
                .push(Spanned::new(branch_span, StmtKind::Seq(joining_blocks)));
        }
        Ok(())
    }
}

impl<'tcx> VisitorMut for Passify<'tcx> {
    type Err = PassifyError;

    fn visit_stmt(&mut self, s: &mut Stmt) -> Result<(), Self::Err> {
        if !matches!(
            s.node,
            StmtKind::Demonic(..)
                | StmtKind::Angelic(..)
                | StmtKind::If(..)
                | StmtKind::Additive(..)
        ) {
            walk_stmt(self, s)?;
        }
        match &mut s.node {
            StmtKind::Assign(idents, expr) => {
                let [ident] = idents.as_slice() else {
                    unreachable!("SpecCall desugaring should have removed all tuple lhses");
                };
                let encoding = self.encode_assign(s.span, *ident, expr)?;
                s.node = StmtKind::Seq(encoding.node);
            }
            StmtKind::Var(_, None) => {}
            StmtKind::Var(decl_ref, Some(initialization)) => {
                let block = {
                    let declaration = decl_ref.borrow();
                    self.encode_assign(s.span, declaration.name, initialization)?
                };
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
            StmtKind::Additive(branch_a, branch_b) => {
                self.visit_branching_stmt(s.span, branch_a, branch_b)?;
                s.node = StmtKind::Additive(branch_a.to_owned(), branch_b.to_owned());
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
            StmtKind::Compare(direction, expr) => {
                s.node = StmtKind::Compare(
                    *direction,
                    self.substitute_in_expr(expr.to_owned(), s.span.variant(SpanVariant::Passify)),
                )
            }
            StmtKind::Weigh(expr) => {
                s.node = StmtKind::Weigh(
                    self.substitute_in_expr(expr.to_owned(), s.span.variant(SpanVariant::Passify)),
                )
            }
            StmtKind::While(..) => {
                // This should already have been desugared through a proof rule. If it still occurs,
                // vcgen will spit out a diagnostic later on, so we allow it to just pass through.
            }
            _ => {}
        };
        Ok(())
    }
}

// return sone Span:variant(SpanVariant::Passify)
