from collections import OrderedDict
from itertools import product, combinations
import sys
from distutils.version import LooseVersion

import rdkit
from rdkit import Chem
from rdkit.Chem import BondType


METALS = (3, 4, 11, 12, 13, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31,
          37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 55, 56, 57,
          58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74,
          75, 76, 77, 78, 79, 80, 81, 82, 83, 87, 88, 89, 90, 91, 92, 93, 94,
          95, 96, 97, 98, 99, 100, 101, 102, 103)


def PathFromAtomList(mol, amap):
    out = []
    for i, j in combinations(amap, 2):
        bond = mol.GetBondBetweenAtoms(i, j)
        if bond:
            out.append(bond.GetIdx())
    return out


def AtomListToSubMol(mol, amap, includeConformer=False):
    """
    Parameters
    ----------
        mol: rdkit.Chem.rdchem.Mol
            Molecule
        amap: array-like
            List of atom indices (zero-based)
        includeConformer: bool (default=True)
            Toogle to include atoms coordinates in submolecule.

    Returns
    -------
        submol: rdkit.Chem.rdchem.RWMol
            Submol determined by specified atom list
    """

    submol = Chem.RWMol()
    for aix in amap:
        submol.AddAtom(mol.GetAtomWithIdx(aix))
    for i, j in combinations(amap, 2):
        bond = mol.GetBondBetweenAtoms(i, j)
        if bond:
            submol.AddBond(amap.index(i),
                           amap.index(j),
                           bond.GetBondType())
    if includeConformer:
        conf = mol.GetConformer(-1)
        new_conf = Chem.Conformer(len(amap))
        for i in range(len(amap)):
            new_conf.SetAtomPosition(i, conf.GetAtomPosition(amap[i]))
        submol.AddConformer(new_conf)
    return submol


def AssignPDBResidueBondOrdersFromTemplate(protein, residue, amap, template):
    """
    Parameters
    ----------
        protein: rdkit.Chem.rdchem.RWMol
            Mol with whole protein. Note that it is modified in place.
        residue:
            Mol with residue only
        amap: dict
            Dictionary mapping atom IDs in residue to atom IDs in whole protein
        template:
            Residue template
    Returns
    -------
        protein: rdkit.Chem.rdchem.RWMol
            Modified protein
        visited_bonds: list
            Bonds that match the template
    """

    # Catch residues which have less than 4 atoms (i.e. cannot have complete
    # backbone), and template has more atoms than that.
    if len(amap) < 4 and template.GetNumAtoms() > 4:
        raise ValueError('The residue "%s" has incomplete backbone'
                         % template.GetProp('_Name'),
                         Chem.MolToSmiles(template),
                         Chem.MolToSmiles(residue))

    # modify copies instead of original molecules
    template2 = Chem.Mol(template)
    residue2 = Chem.Mol(residue)

    visited_bonds = []

    # do the molecules match already?
    matches = residue2.GetSubstructMatches(template2, maxMatches=1)
    if not matches:  # no, they don't match
        # set the bonds orders to SINGLE
        for b in residue2.GetBonds():
            b.SetBondType(BondType.SINGLE)
            b.SetIsAromatic(False)
        for b in template2.GetBonds():
            b.SetBondType(BondType.SINGLE)
            b.SetIsAromatic(False)
        # set atom charges to zero and remove aromaticity
        for a in residue2.GetAtoms():
            a.SetFormalCharge(0)
            a.SetIsAromatic(False)
        for a in template2.GetAtoms():
            a.SetFormalCharge(0)
            a.SetIsAromatic(False)

        # matches is either tuple (if match was complete) or dict (if match
        # was partial)
        matches = residue2.GetSubstructMatches(template2, maxMatches=1)

    # try inverse match
    if not matches:
        inverse_matches = template.GetSubstructMatches(residue, maxMatches=1)
        # if it failed try to match modified molecules (single bonds,
        # no charges, no aromatic atoms)
        if not inverse_matches:
            inverse_matches = template2.GetSubstructMatches(residue2, maxMatches=1)
        if inverse_matches:
            matches = []
            for inverse_match in inverse_matches:
                matches.append(dict(zip(inverse_match,
                                        range(len(inverse_match)))))

    # do the molecules match now?
    if matches:
        for matching in matches:

            assert len(matching) <= len(amap), \
                'matching is bigger than amap for %s' \
                '(%s / %s vs %s; %s atoms vs %s atoms)' % (
                    template.GetProp('_Name'),
                    Chem.MolToSmiles(template),
                    Chem.MolToSmiles(template2),
                    Chem.MolToSmiles(residue),
                    residue.GetNumAtoms(),
                    template.GetNumAtoms(),
            )

            # Convert matches to dict to support partial match, where keys
            # are not complete sequence, as in full match.
            if isinstance(matching, (tuple, list)):
                matching = dict(zip(range(len(matching)), matching))

            # apply matching: set bond properties
            for (atom1, atom2), (refatom1, refatom2) in \
                zip(product(matching.values(), repeat=2),
                    product(matching.keys(), repeat=2)):

                b = template.GetBondBetweenAtoms(refatom1, refatom2)

                b2 = protein.GetBondBetweenAtoms(amap[atom1], amap[atom2])
                # remove extra bonds
                if b is None:
                    if b2:  # this bond is not there
                        protein.RemoveBond(amap[atom1], amap[atom2])
                    continue
                # add missing bonds
                if b2 is None:
                    protein.AddBond(amap[atom1], amap[atom2])
                    b2 = protein.GetBondBetweenAtoms(amap[atom1], amap[atom2])
                # set bond properties
                b2.SetBondType(b.GetBondType())
                b2.SetIsAromatic(b.GetIsAromatic())
                visited_bonds.append((amap[atom1], amap[atom2]))

            # apply matching: set atom properties
            for a in template.GetAtoms():
                if a.GetIdx() not in matching:
                    continue
                a2 = protein.GetAtomWithIdx(amap[matching[a.GetIdx()]])
                a2.SetHybridization(a.GetHybridization())

                # partial match may not close ring, so set aromacity only if
                # there is complete match
                if len(matching) == template.GetNumAtoms():

                    a2.SetIsAromatic(a.GetIsAromatic())
                # TODO: check for connected Hs
                # n_hs = sum(n.GetAtomicNum() == 1 for n in a2.GetNeighbors())
                a2.SetNumExplicitHs(a.GetNumExplicitHs())
                a2.SetFormalCharge(a.GetFormalCharge())
        if len(matching) < template.GetNumAtoms():
            # TODO: replace following with warning/logging
            # Get atom map of fixed fragment
            amap_frag = [amap[matching[a.GetIdx()]]
                         for a in template.GetAtoms()
                         if a.GetIdx() in matching]
            print('Partial match. Probably incomplete sidechain.',
                  template.GetProp('_Name'),
                  Chem.MolToSmiles(template),
                  Chem.MolToSmiles(template2),
                  Chem.MolToSmiles(residue),
                  Chem.MolToSmiles(AtomListToSubMol(protein, amap_frag)),
                  sep='\t', file=sys.stderr)
    else:
        # most common missing sidechain AA
        msg = 'No matching found'
        raise ValueError(msg,
                         template.GetProp('_Name'),
                         Chem.MolToSmiles(template),
                         Chem.MolToSmiles(template2),
                         Chem.MolToSmiles(residue),
                         )

    return protein, visited_bonds


def PreparePDBMol(mol,
                  removeHs=True,
                  removeHOHs=True,
                  residue_whitelist=None,
                  residue_blacklist=None,
                  ):
    """Prepares protein molecule by:
        - Removing Hs by hard using atomic number [default=True]
        - Removes HOH [default=True]
        - Assign bond orders from smiles of PDB residues (over 24k templates)
        - Removes bonds to metals

    Parameters
    ----------
        mol: rdkit.Chem.rdchem.Mol
            Mol with whole protein.
        removeHs: bool, optional (default True)
            If True, hydrogens will be forcefully removed
        removeHOHs: bool, optional (default True)
            If True, remove waters using residue name
        residue_whitelist: array-like, optional (default None)
            List of residues to clean. If not specified, all residues
            present in the structure will be used.
        residue_blacklist: array-like, optional (default None)
            List of residues to ignore during cleaning. If not specified,
            all residues present in the structure will be cleaned.

    Returns
    -------
        new_mol: rdkit.Chem.rdchem.RWMol
            Modified protein
    """

    new_mol = Chem.RWMol(mol)
    if removeHs:
        for i in reversed(range(new_mol.GetNumAtoms())):
            atom = new_mol.GetAtomWithIdx(i)
            if atom.GetAtomicNum() == 1:
                new_mol.RemoveAtom(i)

    if removeHOHs:
        for i in reversed(range(new_mol.GetNumAtoms())):
            atom = new_mol.GetAtomWithIdx(i)
            if atom.GetPDBResidueInfo().GetResidueName() == 'HOH':
                new_mol.RemoveAtom(i)
    # disconnect_metals and HOHs for older versions of RDKit
    # TODO: put here the RDKit version which includes PR #1629
    if LooseVersion(rdkit.__version__) < LooseVersion('2018.03'):
        for i in reversed(range(new_mol.GetNumAtoms())):
            atom = new_mol.GetAtomWithIdx(i)
            atom_info = atom.GetPDBResidueInfo()
            if not removeHOHs and atom_info.GetResidueName() == 'HOH':
                for n in atom.GetNeighbors():
                    n_info = n.GetPDBResidueInfo()
                    if n_info.GetResidueNumber() != atom_info.GetResidueNumber():
                        new_mol.RemoveBond(i, n.GetIdx())
            if atom.GetAtomicNum() in METALS:
                for n in atom.GetNeighbors():
                    new_mol.RemoveBond(i, n.GetIdx())

    # list of unique residues and their atom indices
    unique_resname = set()

    # (res number, res name, chain id) --> [atom1 idx, atom2 idx, ...]
    resiues_atom_map = OrderedDict()

    for atom in new_mol.GetAtoms():
        info = atom.GetPDBResidueInfo()
        res_id = (info.GetResidueNumber(), info.GetResidueName().strip(),
                  info.GetChainId())
        if res_id not in resiues_atom_map:
            resiues_atom_map[res_id] = []
        resiues_atom_map[res_id].append(atom.GetIdx())
        unique_resname.add(info.GetResidueName().strip())

    # create a list of residue mols with atom maps
    residues = []
    # residue_id == (res number, res name, chain id)
    for residue_id, amap in resiues_atom_map.items():
        # skip waters
        if residue_id[1] != 'HOH':
            res = AtomListToSubMol(new_mol, amap)
            residues.append((residue_id, res, amap))

    # load templates
    template_mols = {}
    with open('pdb_residue_templates.smi') as f:
        for n, line in enumerate(f):
            data = line.split()
            # TODO: skip all residues that have 1 heavy atom
            if data[1] in unique_resname and data[1] != 'HOH':  # skip waters
                res = Chem.MolFromSmiles(data[0])
                # remove oxygen for peptide
                # TODO: Remove that and treat the templates accordingly
                match = res.GetSubstructMatch(Chem.MolFromSmiles('OC(=O)CN'))
                if match:
                    res = Chem.RWMol(res)
                    res.RemoveAtom(match[0])
                    res = res.GetMol()

                res.SetProp('_Name', data[1])  # Needed for residue type lookup
                template_mols[data[1]] = res

    # Deal with residue lists
    if residue_whitelist is not None:
        unique_resname = set(residue_whitelist)
    if residue_blacklist is not None:
        unique_resname = unique_resname.difference(set(residue_blacklist))
    unique_resname = tuple(map(lambda x: x.strip().upper(), unique_resname))

    # reset B.O. using templates
    visited_bonds = []
    for ((resnum, resname, chainid), residue, amap) in residues:
        if resname not in unique_resname:
            continue
        if resname not in template_mols:
            raise ValueError('There is no template for residue "%s"' % resname)
        template = template_mols[resname]
        bonds = []
        # in case of error define it here
        try:
            new_mol, bonds = AssignPDBResidueBondOrdersFromTemplate(new_mol,
                                                                    residue,
                                                                    amap,
                                                                    template)
        except ValueError as e:
            print(resnum, resname, chainid, e)
        finally:
            visited_bonds.extend(bonds)

    # HACK: remove not-visited bonds
    if visited_bonds:  # probably we dont want to delete all
        new_mol = Chem.RWMol(new_mol)
        visited_bonds = set(visited_bonds)
        bonds_queue = []
        for bond in new_mol.GetBonds():
            a1 = bond.GetBeginAtomIdx()
            a2 = bond.GetEndAtomIdx()
            if (a1, a2) not in visited_bonds and (a2, a1) not in visited_bonds:
                bonds_queue.append((a1, a2))
        for a1_ix, a2_ix in bonds_queue:
            bond = new_mol.GetBondBetweenAtoms(a1_ix, a2_ix)
            a1 = new_mol.GetAtomWithIdx(a1_ix)
            a2 = new_mol.GetAtomWithIdx(a2_ix)
            # get residue number
            a1_num = a1.GetPDBResidueInfo().GetResidueNumber()
            a2_num = a2.GetPDBResidueInfo().GetResidueNumber()
            # get PDB atom names
            a1_name = a1.GetPDBResidueInfo().GetName().strip()
            a2_name = a2.GetPDBResidueInfo().GetName().strip()
            if (a1.GetAtomicNum() > 1 and
                a2.GetAtomicNum() > 1 and
                # don't remove bonds between residues in backbone
                # and sulphur bridges
                not ((a1_name == 'N' and
                      a2_name == 'C' and
                      abs(a1_num - a2_num) == 1) or  # peptide bond
                     (a1_name == 'C' and
                      a2_name == 'N' and
                      abs(a1_num - a2_num) == 1) or  # peptide bond
                     (a1_name == 'SG' and
                      a2_name == 'SG')  # sulphur bridge
                     )):
                new_mol.RemoveBond(a1_ix, a2_ix)
            else:
                pass

        # HACK: termini oxygens get matched twice due to removal from templates
        # TODO: remove by treatment of templates
        # Terminus treatment
        # for atom in new_mol.GetAtoms():
        #     if atom.GetAtomicNum() == 8 and atom.GetPDBResidueInfo().GetName().strip() == 'OXT':
        #         bonds = atom.GetBonds()
        #         if len(bonds) > 0:  # this should not happen at all
        #             bonds[0].SetBondType(BondType.SINGLE)

    return new_mol