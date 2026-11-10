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

pub struct Passify<'tcx> {
    tcx: &'tcx mut TyCtx,
    direction: Direction,
}

impl<'tcx> Passify<'tcx> {
    pub fn new(tcx: &'tcx mut TyCtx, direction: Direction) -> Self {
        Passify { tcx, direction }
    }

    // Builds a passified version of the assignment if possible.
    pub fn encode_assign(&mut self, span: Span, ident: Ident, expr: &Expr) -> Option<Block> {
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
        let expr_builder = ExprBuilder::new(span);

        let passified_ident = self.tcx.clone_var(ident, span, VarKind::Subst);
        let passified_equals_expr = expr_builder.binary(
            BinOpKind::Eq,
            Some(TyKind::Bool),
            ident_to_expr(self.tcx, span, passified_ident),
            expr.clone(), // TODO this needs to have the subst applied to it
        );
        let mut embedded_passified_equals_expr =
            expr_builder.unary(UnOpKind::Embed, Some(TyKind::Bool), passified_equals_expr);
        if self.direction == Direction::Down {
            embedded_passified_equals_expr = expr_builder.unary(
                UnOpKind::Not,
                Some(TyKind::Bool),
                embedded_passified_equals_expr,
            )
        }

        Some(Block::new(
            span,
            vec![
                Spanned::new(span, StmtKind::Havoc(self.direction, vec![passified_ident])),
                Spanned::new(
                    span,
                    StmtKind::Assume(self.direction, embedded_passified_equals_expr),
                ),
            ],
        ))
    }
}

impl<'tcx> VisitorMut for Passify<'tcx> {
    type Err = ();

    fn visit_stmt(&mut self, s: &mut Stmt) -> Result<(), Self::Err> {
        walk_stmt(self, s)?;
        match &mut s.node {
            StmtKind::Assign(idents, expr) => {
                let [ident] = idents.as_slice() else {
                    unreachable!("SpecCall desugaring should have removed all tuple lhses");
                };
                s.node = StmtKind::Seq(self.encode_assign(s.span, *ident, expr).unwrap().node);
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
            // StmtKind::Demonic
            _ => {}
        };
        Ok(())
    }

    // fn visit_block(&mut self, block: &mut Block) -> Result<(), Self::Err> {
    //     todo!()
    // }
}

// return sone Span:variant(SpanVariant::Passify)
