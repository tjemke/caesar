use crate::{
    ast::{
        diagnostic::{Span, SpanVariant},
        stmt::{Direction, Stmt, StmtKind},
        visit::{walk_stmt, VisitorMut},
        BinOpKind, Block, Expr, ExprBuilder, ExprData, ExprKind, Ident, Spanned, TyKind, UnOpKind,
        VarKind,
    },
    proof_rules::ident_to_expr, // TODO: this needs to be moved somewhere, spec_call already has a copy
    tyctx::TyCtx,
    vc::subst::Subst,
};

pub struct Passify<'tcx> {
    tcx: &'tcx mut TyCtx,
    direction: Direction,
}

impl<'tcx> Passify<'tcx> {
    pub fn new(tcx: &'tcx mut TyCtx, direction: Direction) -> Self {
        Passify { tcx, direction }
    }

    pub fn encode_assign(&mut self, span: Span, ident: Ident, expr: &Expr) -> Option<Block> {
        let span = span.variant(SpanVariant::Passify);
        let expr_builder = ExprBuilder::new(span);

        let passified_ident = self.tcx.clone_var(ident, span, VarKind::Subst);
        let passified_equals_expr = expr_builder.binary(
            BinOpKind::Eq,
            Some(TyKind::Bool),
            ident_to_expr(self.tcx, span, passified_ident),
            expr.clone(), // TODO this needs to have the subst applied to it
        );
        let embedded_passified_equals_expr =
            expr_builder.unary(UnOpKind::Embed, Some(TyKind::Bool), passified_equals_expr);

        Some(Block::new(span, vec![
            Spanned::new(span, StmtKind::Havoc(self.direction, vec![passified_ident])),
            Spanned::new(span, StmtKind::Assume(self.direction, embedded_passified_equals_expr)),
        ]))
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
                self.encode_assign(s.span, *ident, expr);
            }
            // StmtKind::Demonic
            _ => {}
        };
        Ok(())
    }

    fn visit_block(&mut self, block: &mut Block) -> Result<(), Self::Err> {
        todo!()
    }
}

// return sone Span:variant(SpanVariant::Passify)
