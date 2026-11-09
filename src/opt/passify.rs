use crate::{
    ast::{
        Block, Expr, ExprData, ExprKind, Ident, Spanned, VarKind, diagnostic::{Span, SpanVariant}, stmt::{Direction, Stmt, StmtKind}, visit::{VisitorMut, walk_stmt},
    }, tyctx::TyCtx, vc::subst::Subst,
};

pub struct Passify<'tcx> {
    tcx: &'tcx mut TyCtx,
    direction: Direction,
}

impl<'tcx> Passify<'tcx> {
    pub fn new(tcx: &'tcx mut TyCtx, direction: Direction) -> Self {
        Passify { tcx, direction }
    }

    pub fn encode_assign(&mut self, span: Span, idents: &[Ident], expr: &Expr) -> Option<Block> {
        let span = span.variant(SpanVariant::Passify);
        let mut sequence_buffer: Vec<Spanned<StmtKind>> = Vec::new();

        let fresh_idents: Vec<Ident> = idents
            .iter()
            .map(|i| self.tcx.clone_var(*i, span, VarKind::Subst))
            .collect();
        sequence_buffer.append(&mut vec![
            Spanned::new(span, StmtKind::Havoc(self.direction, fresh_idents)),
            Spanned::new(span, StmtKind::Assume(self.direction, Shared::new())),
        ]);
        Expr::new(ExprData{kind: ExprKind::Subst((), (), ())})
        Some(Block::new(span, sequence_buffer))
    }
}

impl<'tcx> VisitorMut for Passify<'tcx> {
    type Err = ();

    fn visit_stmt(&mut self, s: &mut Stmt) -> Result<(), Self::Err> {
        walk_stmt(self, s)?;
        match &mut s.node {
            StmtKind::Assign(idents, expr) => {
                self.encode_assign(s.span, idents, expr);
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
